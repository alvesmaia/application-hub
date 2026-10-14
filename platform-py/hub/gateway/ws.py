"""WebSocket through the gateway: `/apps/{app}/ws/...` becomes a browser <-> upstream tunnel."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import Counter

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hub.gateway.identity import Identity
from hub.gateway.proxy import Gateway, build_upstream_headers
from hub.gateway.registry import AppEntry

_CONNS: Counter[str] = Counter()


def _env(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return v if v > 0 else default


def origin_ok(origin: str | None, host: str | None) -> bool:
    """`Origin` present and with the same authority as `Host` (CSWSH guard)."""
    if not origin or not host:
        return False
    for pre in ("https://", "http://"):
        if origin.startswith(pre):
            return origin[len(pre) :].split("/", 1)[0].lower() == host.lower()
    return False


def _refused(ws: WebSocket) -> bool:
    raw = (ws.scope.get("raw_path") or b"").decode("latin-1").lower()
    return "%25" in raw or "_hub" in raw or not origin_ok(ws.headers.get("origin"), ws.headers.get("host"))


def _target(
    gw: Gateway, ws: WebSocket, app: AppEntry, app_id: str, ident: Identity, rest: str
) -> tuple[str, list[tuple[str, str]]]:
    """Upstream `ws://` URL and the headers that go with the handshake."""
    hdrs = build_upstream_headers(list(ws.headers.items()), app, ident, "producao", gw.opts.user_sig_key)
    skip = ("sec-websocket-", "content-length", "origin", "accept-encoding", "sec-fetch-")
    hdrs = [(k, v) for k, v in hdrs if not k.lower().startswith(skip)]
    base = app.upstream.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    query = ws.scope.get("query_string", b"").decode("latin-1")
    return f"{base}/{app_id}{rest}" + (f"?{query}" if query else ""), hdrs


async def _tunnel(
    gw: Gateway, ws: WebSocket, url: str, hdrs: list[tuple[str, str]], limits: tuple[int, int, int]
) -> None:
    max_msg, idle, max_life = limits
    try:
        upstream = await asyncio.wait_for(
            websockets.connect(url, additional_headers=hdrs, max_size=max_msg, open_timeout=None),
            gw.opts.connect_timeout * 2,
        )
    except Exception:
        await ws.close(code=1013)
        return
    await ws.accept(subprotocol=upstream.subprotocol)
    try:
        await _pump(ws, upstream, idle, max_life)
    finally:
        with contextlib.suppress(Exception):
            await upstream.close()
        if ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await ws.close(code=1001)


async def proxy_ws(gw: Gateway, ws: WebSocket, app_id: str, rest: str) -> None:
    max_per_user = _env("HUB_WS_MAX_PER_USER", 8)
    max_msg = _env("HUB_WS_MAX_MESSAGE_KB", 256) * 1024
    limits = (max_msg, _env("HUB_WS_IDLE_SECS", 60), _env("HUB_WS_MAX_SECS", 900))
    app = gw.registry.get(app_id)
    if app is None or _refused(ws):
        await ws.close(code=1008)  # handshake recusado (403)
        return
    ident = gw.identity_for(ws)
    slot = f"{app_id}\n{ident.user}"
    if _CONNS[slot] >= max_per_user:
        await ws.close(code=1013)
        return
    url, hdrs = _target(gw, ws, app, app_id, ident, rest)
    _CONNS[slot] += 1
    try:
        await _tunnel(gw, ws, url, hdrs, limits)
    finally:
        _CONNS[slot] -= 1
        if _CONNS[slot] <= 0:
            del _CONNS[slot]


async def _client_to_upstream(ws: WebSocket, up: websockets.ClientConnection, idle: int) -> None:
    while True:
        try:
            msg = await asyncio.wait_for(ws.receive(), idle)
        except TimeoutError:
            return
        if msg["type"] == "websocket.disconnect":
            return
        if (t := msg.get("text")) is not None:
            await up.send(t)
        elif (b := msg.get("bytes")) is not None:
            await up.send(b)


async def _upstream_to_client(ws: WebSocket, up: websockets.ClientConnection) -> None:
    try:
        async for m in up:
            if isinstance(m, str):
                await ws.send_text(m)
            else:
                await ws.send_bytes(bytes(m))
    except (websockets.ConnectionClosed, WebSocketDisconnect, RuntimeError):
        return


async def _pump(ws: WebSocket, up: websockets.ClientConnection, idle: int, max_life: int) -> None:
    started = time.monotonic()
    tasks = [
        asyncio.ensure_future(_client_to_upstream(ws, up, idle)),
        asyncio.ensure_future(_upstream_to_client(ws, up)),
    ]
    try:
        await asyncio.wait(tasks, timeout=max_life, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if time.monotonic() - started >= max_life and ws.application_state == WebSocketState.CONNECTED:
        with contextlib.suppress(Exception):
            await ws.close(code=1012)  # maximum lifetime: the client reconnects through the session
