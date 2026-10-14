"""The test sheet (computer): codes to read, live readings and the test-session controls."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk import outbox
from hub_sdk.idempotency import idem_ctx
from hub_sdk.locale import t
from hub_sdk.render import html
from starlette.requests import HTTPConnection

from coletor import fila
from coletor.live import notify
from coletor.model import catalog, locations, orders
from coletor.reads import recent_reads
from coletor.session_store import (
    N_SESSION_CODES,
    load_session,
    rng,
    save_session,
    session_codes,
    session_view,
)
from coletor.state import (
    DB_ERRORS,
    State,
    StateDep,
    db_notice,
    public_base,
    public_url,
    user_login,
    user_name,
)

ACTIONS = ("iniciar", "proximo", "parar", "continuar", "cancelar", "reiniciar", "auto")
# Session actions that only move the queue; they return whether anything changed.
STEPS: dict[str, Callable[[fila.Session, float], bool]] = {
    "proximo": fila.next_code,
    "parar": fila.pause,
    "continuar": fila.resume,
    "cancelar": fila.cancel,
}

router = APIRouter()


def sheet_ctx(req: HTTPConnection) -> dict[str, Any]:
    items = catalog()[:8]
    ords = orders()
    locs = locations()
    samples = [
        ("ean_13", items[0].code),
        ("ean_8", "9638507"),
        ("upc_a", "03600029145"),
        ("code_128", items[1].code128),
        ("code_39", locs[0].code),
        ("qr_code", ords[0].code),
        ("data_matrix", items[2].code128),
    ]
    return {
        "samples": samples,
        "items": items,
        "order": ords[0],
        "locs": locs,
        "app_url": (public_url() + public_base(req)) if public_url() else "",
        "today": dt.date.today(),
        "n_codes": N_SESSION_CODES,
    }


async def folha_state_ctx(c: Any, req: HTTPConnection) -> dict[str, Any]:
    user = user_login(req)
    ctx = sheet_ctx(req)
    ctx |= session_view(await load_session(c, user))
    ctx["reads"] = await recent_reads(c, user, 30)
    ctx["total"] = int(await c.fetchval("SELECT count(*) FROM col_reads WHERE user_name = $1", user))
    return ctx


async def _live(st: State, req: Request, status: int = 200) -> Response:
    async with st.sdk.pool.acquire() as c:
        reads = await recent_reads(c, user_login(req), 30)
        total = int(await c.fetchval("SELECT count(*) FROM col_reads WHERE user_name = $1", user_login(req)))
    return st.render.fragment("live_reads.html", status, reads=reads, total=total)


@router.get("/coletor/folha")
@router.get("/coletor/insumos")
async def folha(req: Request, st: StateDep) -> Response:
    n = db_notice(st)
    ctx: dict[str, Any] = sheet_ctx(req) | {"sess": None, "reads": [], "total": 0}
    if n is None:
        try:
            async with st.sdk.pool.acquire() as c:
                ctx |= await folha_state_ctx(c, req)
        except DB_ERRORS as e:
            n = st.render.notice("error", t("Erro ao consultar as leituras"), str(e))
    return st.render.page(req, "folha.html", "folha", notice_html=n or "", db_ok=n is None, **ctx)


@router.get("/coletor/folha/estado")
async def folha_estado(req: Request, st: StateDep) -> Response:
    if (n := db_notice(st)) is not None:
        return html(str(n), 503)
    async with st.sdk.pool.acquire() as c:
        ctx = await folha_state_ctx(c, req)
    return st.render.fragment("folha_state.html", db_ok=True, **ctx)


@router.get("/coletor/folha/leituras")
async def folha_leituras(req: Request, st: StateDep) -> Response:
    if (n := db_notice(st)) is not None:
        return html(str(n), 503)
    return await _live(st, req)


@router.post("/coletor/folha/limpar")
async def folha_limpar(req: Request, st: StateDep) -> Response:
    if (n := db_notice(st)) is not None:
        return html(str(n), 503)
    async with st.sdk.pool.acquire() as c, c.transaction():
        await c.execute("DELETE FROM col_reads WHERE user_name = $1", user_login(req))
        await outbox.write(c, outbox.audit(user_name(req), "Leituras limpas", user_login(req), ""))
        await notify(c, user_login(req), "read")
        if (ctx := idem_ctx(req)) is not None:
            await ctx.mark_committed(c)
    return await _live(st, req)


async def _act(
    c: Any, req: Request, acao: str, s: fila.Session | None, auto: bool, now: float
) -> tuple[fila.Session | None, bool]:
    """Applies a session action: (session, whether it changed and must be saved)."""
    if acao == "iniciar":
        if s is not None and s.active:
            return s, False
        s = fila.start(session_codes(), rng(), now, auto=auto)
        await outbox.write(c, outbox.audit(user_name(req), "Sessão de teste iniciada", s.id, str(s.total)))
        return s, True
    if s is None:
        return None, False
    if acao == "reiniciar":
        s = fila.restart(s, rng(), now)
        await outbox.write(c, outbox.audit(user_name(req), "Sessão de teste reiniciada", s.id, str(s.total)))
        return s, True
    if acao == "auto":
        fila.set_auto(s, auto)
        return s, True
    return s, STEPS[acao](s, now)


@router.post("/coletor/sessao/{acao}")
async def sessao(acao: str, req: Request, st: StateDep) -> Response:
    """Session controls (computer and phone): the state lives on the server, one per
    user; both sides receive the change over the WebSocket."""
    if acao not in ACTIONS:
        return st.render.not_found()
    if (n := db_notice(st)) is not None:
        return html(str(n), 503)
    form = {k: str(v) for k, v in (await req.form()).items()}
    tela = "escanear" if form.get("tela") == "escanear" else "folha"
    user = user_login(req)
    now = time.time()
    async with st.sdk.pool.acquire() as c, c.transaction():
        s = await load_session(c, user, lock=True)
        s, changed = await _act(c, req, acao, s, form.get("auto") == "1", now)
        if s is not None and changed:
            await save_session(c, user, s)
            await notify(c, user, "session")
        if (ctx := idem_ctx(req)) is not None:
            await ctx.mark_committed(c)
        if tela == "escanear":
            return st.render.fragment("session_phone.html", **session_view(s))
        return st.render.fragment("folha_state.html", db_ok=True, **await folha_state_ctx(c, req))
