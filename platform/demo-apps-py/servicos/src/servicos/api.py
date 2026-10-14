"""API v1 of the ``servicos`` (wave 11): tickets (open, query, change status with optimistic
locking), aggregated management report and pending approval requests (the decision is made through the
hub Inbox — tool ``inbox_decide`` — which calls the app through the internal channel with the
approver's identity). JSON; RFC 9457 errors.

Hub identity required (``x-hub-user``); outbox audit and idempotency
(``Idempotency-Key``) as on the screens. Native FastAPI OpenAPI at ``/servicos/api/openapi.json``;
each operation becomes an MCP tool (``hub_sdk.mcp``).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from hub_sdk.api import DESTRUCTIVE, install_api, problem, require_user
from pydantic import BaseModel, Field

from servicos import report
from servicos.model import (
    CATEGORIES,
    INSERT_SQL,
    PRIORITIES,
    STATUSES,
    TICKET_COLS,
    UPDATE_SQL,
    pick,
    sla_for,
    ticket_json,
)
from servicos.model import now as utcnow

BASE = "/servicos/api/v1"
TITLE = "Central de Serviços — API"
VERSION = "1.0.0"
GRANULARITY = ["dia", "semana", "mes"]


def _plain(typ: str = "string", enum: Any = None) -> Any:
    """Optional parameter with no default value in the schema (``""`` is not part of the enum)."""

    def fix(schema: dict[str, Any]) -> None:
        schema.pop("anyOf", None)
        schema.pop("default", None)
        schema["type"] = typ
        if enum is not None:
            schema["enum"] = list(enum)

    return fix


def _enum(values: Any) -> Any:
    return Field(json_schema_extra={"enum": list(values)})


class TicketIn(BaseModel):
    title: str
    category: str = _enum(CATEGORIES)
    priority: str = _enum(PRIORITIES)
    description: str | None = None
    approval: bool | None = Field(None, description="Pedir aprovação na Inbox dos aprovadores.")


class StatusIn(BaseModel):
    status: str = _enum(STATUSES)
    version: int = Field(description="Versão lida (controle otimista; 409 se mudou).")


def _unavailable(msg: str) -> JSONResponse:
    r = problem(503, msg)
    r.headers["retry-after"] = "2"
    return r


def register_api(app: FastAPI, st: Any) -> None:
    from servicos import app as core

    DBERR = core.DBERR

    def db_ok() -> bool:
        return core.db_notice(st) is None

    @app.exception_handler(RequestValidationError)
    async def _invalid(req: Request, exc: RequestValidationError) -> Response:
        if not req.url.path.startswith(BASE):
            from fastapi.exception_handlers import request_validation_exception_handler

            return await request_validation_exception_handler(req, exc)
        detail = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
        return problem(422, detail)

    @app.get(
        f"{BASE}/tickets",
        operation_id="tickets_list",
        tags=["Chamados"],
        summary="Lista chamados (mais recentes primeiro)",
    )
    async def tickets_list(
        req: Request,
        status: str | None = Query(None, description="Status", json_schema_extra=_plain(enum=list(STATUSES))),
        category: str | None = Query(
            None, description="Categoria", json_schema_extra=_plain(enum=list(CATEGORIES))
        ),
        protocol: str | None = Query(
            None, description="Protocolo exato (SD-AAAA-n)", json_schema_extra=_plain()
        ),
        limit: str | None = Query(
            None, description="Até 100 (padrão 25)", json_schema_extra=_plain("integer")
        ),
    ) -> Response:
        if isinstance(u := require_user(req), JSONResponse):
            return u
        if not db_ok():
            return _unavailable("banco de dados indisponível ou não configurado")
        stat, cat = pick((status or "").strip(), STATUSES), pick((category or "").strip(), CATEGORIES)
        try:
            lim = max(1, min(100, int((limit or "").strip())))
        except ValueError:
            lim = 25
        try:
            async with st.sdk.pool.acquire() as c:
                rows = await c.fetch(
                    f"SELECT {TICKET_COLS} FROM tickets WHERE ($1::text = '' OR status = $1) "
                    "AND ($2::text = '' OR category = $2) AND ($3::text = '' OR protocol = $3) "
                    f"ORDER BY id DESC LIMIT {lim}",
                    stat,
                    cat,
                    (protocol or "").strip()[:40],
                )
        except DBERR as e:
            return _unavailable(str(e))
        items = [ticket_json(r) for r in rows]
        return JSONResponse({"total": len(items), "limit": lim, "items": items})

    @app.get(
        f"{BASE}/tickets/{{id}}",
        operation_id="tickets_get",
        tags=["Chamados"],
        summary="Um chamado",
    )
    async def tickets_get(req: Request, id: int) -> Response:
        if isinstance(u := require_user(req), JSONResponse):
            return u
        if not db_ok():
            return _unavailable("banco de dados indisponível ou não configurado")
        try:
            async with st.sdk.pool.acquire() as c:
                row = await c.fetchrow(f"SELECT {TICKET_COLS} FROM tickets WHERE id = $1", id)
        except DBERR as e:
            return _unavailable(str(e))
        return JSONResponse(ticket_json(row)) if row else problem(404, "chamado não encontrado")

    @app.post(
        f"{BASE}/tickets",
        operation_id="tickets_open",
        tags=["Chamados"],
        summary="Abre um chamado (opcional: pedir aprovação na Inbox)",
        status_code=201,
    )
    async def tickets_open(req: Request, body: TicketIn) -> Response:
        if isinstance(u := require_user(req), JSONResponse):
            return u
        if not db_ok():
            return _unavailable("banco de dados indisponível ou não configurado")
        title = body.title.strip()[:160]
        cat, pri = pick(body.category, CATEGORIES), pick(body.priority, PRIORITIES)
        desc = (body.description or "")[:4000]
        if not title or not cat or not pri or any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
            return problem(422, "title, category e priority são obrigatórios (valores válidos no OpenAPI)")
        user = core.user_of(req)
        login = core.identity_of(req).user or user
        needs = body.approval is True
        first = "Aguardando" if needs else "Aberto"
        due = utcnow() + sla_for(pri)
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                row = await c.fetchrow(INSERT_SQL, title, user, cat, pri, desc, due, first)
                proto, tid = row["protocol"], row["id"]
                await core.audit_tx(c, req, "Chamado aberto (API)", proto, f"{cat} · {pri} · {title}")
                if needs:
                    await core.request_approval(c, tid, proto, title, login, user)
        except DBERR as e:
            return _unavailable(str(e))
        return JSONResponse(
            {
                "id": tid,
                "protocol": proto,
                "status": first,
                "sla_due": due.isoformat(),
                "approval_requested": needs,
            },
            status_code=201,
        )

    @app.put(
        f"{BASE}/tickets/{{id}}/status",
        operation_id="tickets_set_status",
        tags=["Chamados"],
        summary="Muda o status de um chamado (envie a versão lida)",
        openapi_extra=DESTRUCTIVE,
    )
    async def tickets_set_status(req: Request, id: int, body: StatusIn) -> Response:
        if isinstance(u := require_user(req), JSONResponse):
            return u
        if not db_ok():
            return _unavailable("banco de dados indisponível ou não configurado")
        status = pick(body.status, STATUSES)
        if not status:
            return problem(422, "status inválido")
        if not -(2**31) <= body.version < 2**31:
            return problem(409, "versão desatualizada ou chamado inexistente: leia de novo")
        try:
            async with st.sdk.pool.acquire() as c, c.transaction():
                row = await c.fetchrow(UPDATE_SQL, id, body.version, status)
                if row is not None:
                    await core.audit_tx(
                        c, req, "Chamado atualizado (API)", row["protocol"], f"status: {status}"
                    )
        except DBERR as e:
            if core.transient(e):
                return _unavailable("banco indisponível; nada foi confirmado")
            return problem(500, str(e))
        if row is None:
            return problem(409, "versão desatualizada ou chamado inexistente: leia de novo")
        return JSONResponse(ticket_json(row))

    @app.get(
        f"{BASE}/reports/summary",
        operation_id="reports_summary",
        tags=["Relatórios"],
        summary="Relatório agregado por período e categoria",
    )
    async def reports_summary(
        req: Request,
        de: str | None = Query(None, description="Início (AAAA-MM-DD)", json_schema_extra=_plain()),
        ate: str | None = Query(None, description="Fim (AAAA-MM-DD)", json_schema_extra=_plain()),
        agrupar: str | None = Query(
            None, description="Granularidade", json_schema_extra=_plain(enum=GRANULARITY)
        ),
        categoria: str | None = Query(
            None, description="Categoria", json_schema_extra=_plain(enum=list(CATEGORIES))
        ),
    ) -> Response:
        if isinstance(u := require_user(req), JSONResponse):
            return u
        if not db_ok():
            return _unavailable("banco de dados indisponível ou não configurado")
        p = report.Params.from_query(
            {"de": de or "", "ate": ate or "", "agrupar": agrupar or "", "categoria": categoria or ""}
        )
        permit = st.sdk.heavy.try_acquire()
        if permit is None:
            r = problem(429, "muitos relatórios em andamento; tente em instantes")
            r.headers["retry-after"] = "5"
            return r
        try:
            with permit:
                res = await report.run(st.sdk.pool, p)
        except DBERR as e:
            return _unavailable(str(e))
        return JSONResponse(
            {
                "from": str(p.from_),
                "to": str(p.to),
                "group_by": p.gran,
                "category": p.category,
                "elapsed_ms": res.elapsed_ms,
                "rows": [
                    {
                        "category": x.category,
                        "period": str(x.period),
                        "total": x.total,
                        "resolved": x.resolved,
                        "avg_hours": x.avg_hours,
                        "within_sla": x.within_sla,
                    }
                    for x in res.rows
                ],
            }
        )

    @app.get(
        f"{BASE}/approvals",
        operation_id="approvals_pending",
        tags=["Aprovações"],
        summary="Pedidos de aprovação pendentes (decisão pela Inbox do hub)",
    )
    async def approvals_pending(req: Request) -> Response:
        if isinstance(u := require_user(req), JSONResponse):
            return u
        if not db_ok():
            return _unavailable("banco de dados indisponível ou não configurado")
        try:
            async with st.sdk.pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT a.ticket_id, t.protocol, t.title, a.requested_by_name, a.created_at "
                    "FROM ticket_approvals a JOIN tickets t ON t.id = a.ticket_id "
                    "WHERE a.status = 'pending' ORDER BY a.created_at DESC LIMIT 100"
                )
        except DBERR as e:
            return _unavailable(str(e))
        items = [
            {
                "ticket_id": r[0],
                "protocol": r[1],
                "title": r[2],
                "requested_by": r[3],
                "requested_at": r[4].isoformat(),
                "inbox_item": f"sd-{r[0]}",
            }
            for r in rows
        ]
        return JSONResponse({"total": len(items), "items": items})

    install_api(app, "servicos", title=TITLE, version=VERSION)
