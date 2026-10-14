"""Real time: the user's screen fragment pushed over a WebSocket on each reading/session change."""

from __future__ import annotations

import asyncio
import contextlib
import json

import asyncpg
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from hub_sdk import locale as _locale

from coletor.live import LIVE
from coletor.routes.sheet import folha_state_ctx
from coletor.session_store import load_session, session_view
from coletor.state import State, StateDep, db_notice, user_login

router = APIRouter()


async def ws_payload(st: State, ws: WebSocket, view: str) -> str:
    async with st.sdk.pool.acquire() as c:
        if view == "escanear":
            frag = st.render.render(
                "session_phone.html", **session_view(await load_session(c, user_login(ws)))
            )
            target = "#col-session"
        else:
            frag = st.render.render("folha_state.html", db_ok=True, **await folha_state_ctx(c, ws))
            target = "#col-folha-state"
    return json.dumps({"target": target, "swap": "outerHTML", "html": frag})


async def _drain_client(ws: WebSocket) -> None:
    """Reads (and ignores) what the client sends; ends when it disconnects."""
    with contextlib.suppress(WebSocketDisconnect, RuntimeError):
        while True:
            await ws.receive_text()


async def _push(st: State, ws: WebSocket, view: str, q: asyncio.Queue[str], rx: asyncio.Task[None]) -> None:
    await ws.send_text(await ws_payload(st, ws, view))
    while not rx.done():
        get = asyncio.create_task(q.get())
        done, _ = await asyncio.wait({get, rx}, return_when=asyncio.FIRST_COMPLETED)
        if get not in done:
            get.cancel()
            break
        while not q.empty():  # bursts of notices → one redraw
            q.get_nowait()
        await ws.send_text(await ws_payload(st, ws, view))


@router.websocket("/coletor/ws/live")
async def ws_live(ws: WebSocket, st: StateDep) -> None:
    """Live state of the user: on each reading/session change, the screen fragment
    (``view=folha`` or ``escanear``) is redrawn and sent (hub-kit ``data-hub-ws``)."""
    view = "escanear" if ws.query_params.get("view") == "escanear" else "folha"
    user = user_login(ws)
    if user == "anonimo":
        await ws.close(code=1008)
        return
    if db_notice(st) is not None:
        await ws.close(code=1013)
        return
    _locale.use(_locale.from_headers(ws.headers))  # the socket task has its own context
    await ws.accept()
    await LIVE.ensure_listener(getattr(st.sdk.db, "url", None))
    q = LIVE.subscribe(user)
    rx = asyncio.create_task(_drain_client(ws))
    try:
        await _push(st, ws, view, q, rx)
    except (WebSocketDisconnect, RuntimeError, OSError, asyncpg.PostgresError):
        pass
    finally:
        rx.cancel()
        LIVE.unsubscribe(user, q)
        with contextlib.suppress(RuntimeError):
            await ws.close()
