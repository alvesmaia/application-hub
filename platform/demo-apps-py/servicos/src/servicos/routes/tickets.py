"""Tickets: list (key-based pagination), open and change status (optimistic locking)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.identity import identity_of
from hub_sdk.render import html

from servicos import views
from servicos.approvals import request_approval
from servicos.model import (
    CATEGORIES,
    INSERT_SQL,
    PAGE,
    PRIORITIES,
    STATUSES,
    TICKET_COLS,
    UPDATE_SQL,
    pick,
    sla_for,
)
from servicos.model import now as utcnow
from servicos.state import (
    DBERR,
    State,
    StateDep,
    audit_tx,
    db_notice,
    session_key,
    to_i64,
    transient,
    user_of,
    with_cookie,
)
from servicos.ui import notice, param, url_enc, wrap
from servicos.views import not_found, retry_after

router = APIRouter()


@dataclass(frozen=True)
class TicketFilter:
    """The list's query string (``status``, ``categoria``, ``prioridade``, ``protocolo``, ``antes``)."""

    status: str
    cat: str
    pri: str
    proto: str
    before: int | None

    @classmethod
    def from_query(cls, q: Mapping[str, str]) -> TicketFilter:
        return cls(
            pick(param(q, "status"), STATUSES),
            pick(param(q, "categoria"), CATEGORIES),
            pick(param(q, "prioridade"), PRIORITIES),
            param(q, "protocolo")[:40],
            to_i64(param(q, "antes")),
        )

    def where(self) -> tuple[str, list[Any]]:
        """``WHERE`` clause and its positional parameters."""
        conds: list[str] = []
        ps: list[Any] = []
        for col, v in (("status", self.status), ("category", self.cat), ("priority", self.pri)):
            if v:
                ps.append(v)
                conds.append(f"{col} = ${len(ps)}")
        if self.proto:
            ps.append(self.proto.upper())
            conds.append(f"protocol = ${len(ps)}")
        if self.before is not None:
            ps.append(self.before)
            conds.append(f"id < ${len(ps)}")
        return (f"WHERE {' AND '.join(conds)}" if conds else ""), ps


async def _fetch_page(st: State, f: TicketFilter) -> tuple[list[Any] | None, str]:
    """One page of tickets, or ``None`` and the database error."""
    wh, ps = f.where()
    try:
        async with st.sdk.pool.acquire() as c:
            return list(
                await c.fetch(f"SELECT {TICKET_COLS} FROM tickets {wh} ORDER BY id DESC LIMIT {PAGE}", *ps)
            ), ""
    except DBERR as e:
        return None, str(e)


async def _list_body(st: State, q: Mapping[str, str]) -> str:
    f = TicketFilter.from_query(q)
    rows, err = await _fetch_page(st, f)
    body = views.heading_and_stats(await st.status_counts())
    if param(q, "new") == "1":
        body += views.new_form()
    body += views.filters(f.proto, f.status, f.cat, f.pri)
    if rows is not None:
        body += views.table(rows, views.base_query(f.status, f.cat, f.pri, f.proto), f.before is not None)
    else:
        body += notice("error", "Erro ao consultar os chamados", err)
    return body


@router.get("/servicos/requests")
async def requests_page(req: Request, st: StateDep) -> Response:
    key, new_sid = session_key(req)
    q = dict(req.query_params)
    st.sessions.touch(key, lambda s: setattr(s, "last_filters", repr(q)))
    n = db_notice(st)
    if n is not None:
        t = st.page("requests", req) or ""
        marker = '<div class="app3-request-stats">'
        return with_cookie(html(t.replace(marker, f"{n}{marker}", 1)), new_sid)
    body = await _list_body(st, q)
    tpl = st.page("requests", req) or ""
    return with_cookie(html(wrap(tpl, f'<div class="app3-page">{body}</div>')), new_sid)


def _opened(proto: str, due: Any, needs_approval: bool) -> Response:
    extra = " · aguardando aprovação na Inbox" if needs_approval else ""
    return html(
        '<section class="app3-panel hub-panel" id="ticket-new">'
        + notice("ok", "Chamado aberto", f"Protocolo {proto} · SLA até {due:%d/%m %H:%M}{extra}")
        + '<div class="modal-actions"><a class="secondary-button" '
        f'href="/apps/servicos/requests?protocolo={url_enc(proto)}">Ver chamado</a>'
        '<a class="app3-primary" href="/apps/servicos/requests?new=1">Abrir outro</a></div></section>',
        201,
    )


@router.post("/servicos/requests")
async def open_ticket(req: Request, st: StateDep) -> Response:  # noqa: PLR0915
    n = db_notice(st)
    if n is not None:
        return html(views.new_form(n), 503)
    f = {k: str(v) for k, v in (await req.form()).items()}
    title = param(f, "title")[:160]
    cat = pick(param(f, "category"), CATEGORIES)
    pri = pick(param(f, "priority"), PRIORITIES)
    desc = param(f, "description")[:4000]
    if not title or not cat or not pri or any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
        msg = notice("error", "Verifique os campos", "Título, categoria e prioridade são obrigatórios.")
        return html(views.new_form(msg), 422)
    user = user_of(req)
    login = identity_of(req).user or user
    needs_approval = param(f, "approval") == "1"
    first = "Aguardando" if needs_approval else "Aberto"
    due = utcnow() + sla_for(pri)
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            row = await c.fetchrow(INSERT_SQL, title, user, cat, pri, desc, due, first)
            proto, tid = row["protocol"], row["id"]
            await audit_tx(c, req, "Chamado aberto", proto, f"{cat} · {pri} · {title}")
            if needs_approval:
                await request_approval(c, tid, proto, title, login, user)
    except DBERR as e:
        msg = notice("error", "Não foi possível abrir o chamado", str(e))
        return html(views.new_form(msg), 503)
    return _opened(proto, due, needs_approval)


def _cell(msg: str) -> str:
    return f'<div class="hub-row-msg">{msg}</div>'


async def _status_form(req: Request) -> tuple[str, int]:
    """(new status, version the user read); an out-of-range version counts as 0."""
    f = {k: str(v) for k, v in (await req.form()).items()}
    sent = to_i64(param(f, "version"))
    return pick(param(f, "status"), STATUSES), sent if sent is not None and sent < 2**31 else 0


def _update_failed(e: BaseException) -> Response:
    if transient(e):
        msg = notice("error", "Banco indisponível", "Nada foi confirmado. Tente novamente em instantes.")
        return retry_after(html(msg, 503))
    return html(notice("error", "Erro ao atualizar", str(e)), 500)


async def _conflict(st: State, ident: int, sent: int) -> Response:
    """The update matched nothing: the ticket is gone (404) or someone changed it first (409)."""
    try:
        async with st.sdk.pool.acquire() as c:
            cur = await c.fetchrow(f"SELECT {TICKET_COLS} FROM tickets WHERE id = $1", ident)
    except DBERR:
        cur = None
    if cur is None:
        return html(notice("warn", "Chamado não encontrado", ""), 404)
    msg = notice(
        "error",
        "Chamado alterado por outra pessoa",
        f"Você atualizou a versão {sent}, mas a atual é {cur['version']} ({cur['status']}). "
        "Nada foi gravado; revise e tente de novo.",
    )
    return html(views.row_html(cur, _cell(msg)), 409)


@router.api_route("/servicos/requests/{tid}", methods=["POST", "GET"])
async def update_ticket(req: Request, tid: str, st: StateDep) -> Response:
    ident = to_i64(tid)
    if req.method == "GET" or ident is None:
        return not_found()
    n = db_notice(st)
    if n is not None:
        return html(n, 503)
    status, sent = await _status_form(req)
    if not status:
        return html(notice("error", "Status inválido", ""), 422)
    try:
        async with st.sdk.pool.acquire() as c, c.transaction():
            row = await c.fetchrow(UPDATE_SQL, ident, sent, status)
            if row is not None:
                await audit_tx(c, req, "Chamado atualizado", row["protocol"], f"status: {status}")
    except DBERR as e:
        return _update_failed(e)
    if row is None:
        return await _conflict(st, ident, sent)
    return html(views.row_html(row, _cell(notice("ok", "Atualizado", f"Versão {row['version']}"))))
