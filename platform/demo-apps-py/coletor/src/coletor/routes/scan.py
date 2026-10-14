"""The Scan screen (phone): readings, reader preferences and the phone side of the test session."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk import telemetry
from hub_sdk.idempotency import idem_ctx
from hub_sdk.locale import t
from hub_sdk.render import html
from starlette.requests import HTTPConnection

from coletor import fila
from coletor.live import notify
from coletor.reads import (
    DEFAULT_MODE,
    MODES,
    classify,
    clean_value,
    feedback_of,
    load_reader,
    mode_ctx,
    read_format,
    recent_reads,
    save_reader,
    scan_mode,
    store_read,
)
from coletor.session_store import load_session, save_session, session_view
from coletor.state import DB_ERRORS, State, StateDep, db_notice, user_login

router = APIRouter()


async def scan_ctx(c: Any, req: HTTPConnection) -> dict[str, Any]:
    user = user_login(req)
    rd = await load_reader(c, user)
    ctx = mode_ctx(await scan_mode(c, user), rd)
    ctx |= session_view(await load_session(c, user))
    return ctx


async def _lookup(c: Any, req: Request) -> dict[str, Any] | None:
    """``?codigo=``: what that code is (the search results link here)."""
    want = clean_value(req.query_params.get("codigo", ""))
    if not want:
        return None
    kind, label = await classify(c, want)
    return {"value": want, "kind": kind, "label": label, "feedback": feedback_of(kind)}


async def _fill_scan(st: State, req: Request, ctx: dict[str, Any]) -> None:
    """Fills ``ctx`` in place (a database error midway keeps what was already read)."""
    async with st.sdk.pool.acquire() as c, telemetry.db("SELECT col_reads"):
        ctx |= await scan_ctx(c, req)
        ctx["reads"] = await recent_reads(c, user_login(req), 10)
        ctx["last"] = ctx["reads"][0] if ctx["reads"] else None
        if (lookup := await _lookup(c, req)) is not None:
            ctx["lookup"] = lookup


@router.get("/coletor")
@router.get("/coletor/")
@router.get("/coletor/dashboard")
@router.get("/coletor/escanear")
@router.get("/coletor/contagem")
@router.get("/coletor/recebimento")
@router.get("/coletor/consulta")
async def escanear(req: Request, st: StateDep) -> Response:
    n = db_notice(st)
    ctx: dict[str, Any] = {
        "notice_html": n or "",
        "reads": [],
        "last": None,
        "lookup": None,
        "sess": None,
    }
    ctx |= mode_ctx(DEFAULT_MODE)
    if n is None:
        try:
            await _fill_scan(st, req, ctx)
        except DB_ERRORS as e:
            ctx["notice_html"] = st.render.notice("error", t("Erro ao consultar as leituras"), str(e))
    return st.render.page(req, "escanear.html", "escanear", **ctx)


async def _advance_session(c: Any, user: str, value: str, now: float) -> str | None:
    """Feeds an accepted reading to the user's active test session; the outcome, if it counted."""
    sess = await load_session(c, user, lock=True)
    if sess is None or not sess.active:
        return None
    outcome = fila.on_read(sess, value, now)
    if outcome == "ignorado":
        return None
    await save_session(c, user, sess)
    await notify(c, user, "session")
    return outcome


async def _register(
    c: Any, req: Request, value: str, fmt: str, source: str
) -> tuple[fila.Reader, str, dict[str, Any] | None, str | None]:
    """In the reading's transaction: (reader, gate, stored reading or ``None`` if refused, session outcome)."""
    user = user_login(req)
    now = time.time()
    rd = await load_reader(c, user, lock=True)
    gate = fila.reader_gate(rd, value, now)
    last: dict[str, Any] | None = None
    outcome: str | None = None
    if gate == "aceita":
        await save_reader(c, user, rd)
        last = await store_read(c, req, value, fmt, source)
        outcome = await _advance_session(c, user, value, now)
        if outcome == fila.ERRO:
            last["feedback"] = "error"
    if (ctx := idem_ctx(req)) is not None:
        await ctx.mark_committed(c)
    return rd, gate, last, outcome


async def _empty_read(st: State, user: str) -> Response:
    async with st.sdk.pool.acquire() as c:
        reads = await recent_reads(c, user, 10)
        rd = await load_reader(c, user)
    return st.render.fragment("scan_result.html", 422, last=None, reads=reads, empty=True, reader=rd)


@router.post("/coletor/escanear/ler")
async def escanear_ler(req: Request, st: StateDep) -> Response:
    if (n := db_notice(st)) is not None:
        return html(str(n), 503)
    form = {k: str(v) for k, v in (await req.form()).items()}
    value = clean_value(form.get("codigo", ""))
    user = user_login(req)
    if not value:
        return await _empty_read(st, user)
    fmt, source = read_format(form, value)
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            rd, gate, last, outcome = await _register(c, req, value, fmt, source)
        async with st.sdk.pool.acquire() as c:
            reads = await recent_reads(c, user, 10)
    except DB_ERRORS as e:
        return html(str(st.render.notice("error", t("Erro ao registrar a leitura"), str(e))), 503)
    if last is None:
        # Refused by the reader mode (waiting for "Next", paused or repeated).
        prev = reads[0] if reads else None
        return st.render.fragment(
            "scan_result.html", last=prev, reads=reads, empty=False, gate=gate, reader=rd, fresh=False
        )
    return st.render.fragment(
        "scan_result.html", last=last, reads=reads, empty=False, gate=gate, reader=rd, outcome=outcome
    )


@router.post("/coletor/escanear/proximo")
async def escanear_proximo(req: Request, st: StateDep) -> Response:
    if (n := db_notice(st)) is not None:
        return html(str(n), 503)
    user = user_login(req)
    async with st.sdk.pool.acquire() as c, c.transaction():
        rd = await load_reader(c, user, lock=True)
        fila.reader_next(rd)
        await save_reader(c, user, rd)
        if (ctx := idem_ctx(req)) is not None:
            await ctx.mark_committed(c)
        reads = await recent_reads(c, user, 10)
    return st.render.fragment(
        "scan_result.html",
        last=reads[0] if reads else None,
        reads=reads,
        empty=False,
        reader=rd,
        fresh=False,
    )


def _apply_prefs(rd: fila.Reader, form: dict[str, str]) -> None:
    if "leitor" in form:
        new = fila.clean_reader_mode(form["leitor"])
        if new != rd.mode:
            rd.mode = new
            rd.hold, rd.last_value, rd.last_at = False, "", 0.0
    if "pausa" in form:
        rd.cooldown_ms = fila.clean_cooldown(form["pausa"])


@router.post("/coletor/escanear/prefs")
@router.post("/coletor/escanear/modo")
@router.post("/coletor/escanear/leitor")
async def escanear_prefs(req: Request, st: StateDep) -> Response:
    """Choices made INSIDE the camera (top bar of the hub layer, `data-hub-scan-prefs`):
    barcode type (`modo`), reader mode (`leitor`) and pause (`pausa`), per user. Missing
    fields stay as they are. `/modo` and `/leitor` are the old names (same route)."""
    form = {k: str(v) for k, v in (await req.form()).items()}
    if (n := db_notice(st)) is not None:
        return html(str(n), 503)
    user = user_login(req)
    async with st.sdk.pool.acquire() as c, c.transaction():
        if "modo" in form:
            mode = form["modo"] if form["modo"] in MODES else DEFAULT_MODE
            await c.execute(
                "INSERT INTO col_prefs (user_name, scan_mode) VALUES ($1, $2) "
                "ON CONFLICT (user_name) DO UPDATE SET scan_mode = $2",
                user,
                mode,
            )
        rd = await load_reader(c, user, lock=True)
        _apply_prefs(rd, form)
        await save_reader(c, user, rd)
        if (ctx := idem_ctx(req)) is not None:
            await ctx.mark_committed(c)
    return Response(status_code=204)


@router.get("/coletor/escanear/sessao")
async def escanear_sessao(req: Request, st: StateDep) -> Response:
    if (n := db_notice(st)) is not None:
        return html(str(n), 503)
    async with st.sdk.pool.acquire() as c:
        v = session_view(await load_session(c, user_login(req)))
    return st.render.fragment("session_phone.html", **v)
