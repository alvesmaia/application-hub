"""WebSocket pelo gateway: `/apps/{app}/ws/...` vira túnel navegador <-> upstream (port de ws.rs)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections import Counter

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from hub.gateway.proxy import Gateway, build_upstream_headers

_CONNS: Counter[str] = Counter()


def _env(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return v if v > 0 else default


def origin_ok(origin: str | None, host: str | None) -> bool:
    """`Origin` presente e com a mesma autoridade do `Host` (barra CSWSH)."""
    if not origin or not host:
        return False
    for pre in ("https://", "http://"):
        if origin.startswith(pre):
            return origin[len(pre) :].split("/", 1)[0].lower() == host.lower()
    return False


async def proxy_ws(gw: Gateway, ws: WebSocket, app_id: str, rest: str) -> None:
    max_per_user = _env("HUB_WS_MAX_PER_USER", 8)
    max_msg = _env("HUB_WS_MAX_MESSAGE_KB", 256) * 1024
    idle = _env("HUB_WS_IDLE_SECS", 60)
    max_life = _env("HUB_WS_MAX_SECS", 900)
    app = gw.registry.get(app_id)
    raw = (ws.scope.get("raw_path") or b"").decode("latin-1").lower()
    if (
        app is None
        or "%25" in raw
        or "_hub" in raw
        or not origin_ok(ws.headers.get("origin"), ws.headers.get("host"))
    ):
        await ws.close(code=1008)  # handshake recusado (403)
        return
    ident = gw.identity_for(ws)
    slot = f"{app_id}\n{ident.user}"
    if _CONNS[slot] >= max_per_user:
        await ws.close(code=1013)
        return
    hdrs = build_upstream_headers(list(ws.headers.items()), app, ident, "producao", gw.opts.user_sig_key)
    skip = ("sec-websocket-", "content-length", "origin", "accept-encoding", "sec-fetch-")
    hdrs = [(k, v) for k, v in hdrs if not k.lower().startswith(skip)]
    base = app.upstream.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    query = ws.scope.get("query_string", b"").decode("latin-1")
    url = f"{base}/{app_id}{rest}" + (f"?{query}" if query else "")
    _CONNS[slot] += 1
    try:
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
    finally:
        _CONNS[slot] -= 1
        if _CONNS[slot] <= 0:
            del _CONNS[slot]


async def _pump(ws: WebSocket, up: websockets.ClientConnection, idle: int, max_life: int) -> None:
    started = time.monotonic()

    async def c2u() -> None:
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

    async def u2c() -> None:
        try:
            async for m in up:
                if isinstance(m, str):
                    await ws.send_text(m)
                else:
                    await ws.send_bytes(bytes(m))
        except (websockets.ConnectionClosed, WebSocketDisconnect, RuntimeError):
            return

    tasks = [asyncio.ensure_future(c2u()), asyncio.ensure_future(u2c())]
    try:
        await asyncio.wait(tasks, timeout=max_life, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if time.monotonic() - started >= max_life and ws.application_state == WebSocketState.CONNECTED:
        with contextlib.suppress(Exception):
            await ws.close(code=1012)  # vida máxima: o cliente reconecta pela sessão
