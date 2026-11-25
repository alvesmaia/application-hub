"""Administration › Users › Database access (`/admin/users?tab=db`) and `/api/admin/db-access*`: read accounts for
dashboards and tools like DBeaver (hub/admin/dbaccess.py)."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from hub.admin import dbaccess_net as net
from hub.admin.adminctx import AdminCtx, body, ctx, dumps, parse
from hub.admin.config import AdminError
from hub.admin.dbaccess import DbAccess
from hub.admin.dbaccess_sql import NewAccount

api = APIRouter()
pages = APIRouter()
_STATE_CSS = {"ativa": "status-pill status-active", "suspensa": "status-pill status-maintenance",
              "expirada": "status-pill status-draft"}  # fmt: skip


class AccountIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=2, max_length=41)
    owner: str = Field(min_length=1)
    dbs: list[str] = Field(min_length=1)
    days: int = Field(default=90, ge=1, le=365)


def service(request: Request) -> DbAccess:
    """The read accounts over the database container's psql (tests put their own in `app.state.dbaccess`)."""
    if (svc := getattr(request.app.state, "dbaccess", None)) is not None:
        return cast(DbAccess, svc)
    ops = getattr(request.app.state, "ops_ctx", None)
    if ops is None:
        raise AdminError("Acesso ao banco indisponível: o hub não controla o contêiner do banco.", 503)
    b = ops.backups
    return DbAccess(lambda db, stmts: b._psql(db, *stmts))


def conn_info(request: Request) -> dict[str, Any]:
    cfg = net.from_env()
    state = getattr(request.app.state, "dbaccess_state", {}) or {}
    return {"public": cfg.public, "host": cfg.host, "port": cfg.port, "cidrs": list(cfg.cidrs),
            "bad_cidrs": list(cfg.bad_cidrs), "fingerprint": state.get("fingerprint", ""), "error": state.get("error", "")}  # fmt: skip


async def _owner_ok(c: AdminCtx, uid: str) -> dict[str, Any]:
    u = await c.user(uid)
    if u is None or not u["active"]:
        raise AdminError("Responsável inválido: escolha um usuário ativo.", 400)
    return u


@api.get("/db-access")
async def list_accounts(request: Request) -> dict[str, Any]:
    svc = service(request)
    try:
        accounts, dbs = await svc.accounts(), await svc.databases()
    except Exception as e:
        raise AdminError(f"Banco indisponível: {e}"[:200], 503) from e
    return {"connection": conn_info(request), "databases": dbs,
            "accounts": [a | {"until": a["until"].isoformat() if a["until"] else None} for a in accounts]}  # fmt: skip


@api.post("/db-access", status_code=201)
async def create_account(request: Request, c: AdminCtx = Depends(ctx)) -> dict[str, Any]:
    i = parse(AccountIn, await body(request))
    owner = await _owner_ok(c, i.owner)
    a = NewAccount(
        i.name.strip().lower(), owner["id"], tuple(i.dbs), i.days, request.state.user.get("name", "")
    )
    try:
        role, password = await service(request).create(a)
    except ValueError as e:
        raise AdminError(str(e), 400) from e
    await c.audit(request, "users", "Conta de leitura do banco criada",
                  f"{role} · responsável {owner['email']} · bancos {', '.join(a.dbs)} · {a.days} dias")  # fmt: skip
    return {"role": role, "password": password, "connection": conn_info(request)}


@api.post("/db-access/{role}/password")
async def new_password(request: Request, role: str, c: AdminCtx = Depends(ctx)) -> dict[str, Any]:
    svc = service(request)
    acc = next((a for a in await svc.accounts() if a["role"] == role), None)
    if acc is None:
        raise AdminError("Conta de leitura não encontrada.", 404)
    await _owner_ok(c, acc["owner"])  # a suspended account comes back only with an active person responsible
    password = await svc.new_password(role)
    await c.audit(request, "users", "Nova senha de conta de leitura do banco", role)
    return {"role": role, "password": password, "connection": conn_info(request)}


@api.delete("/db-access/{role}", status_code=204)
async def remove_account(request: Request, role: str, c: AdminCtx = Depends(ctx)) -> Response:
    try:
        await service(request).remove(role)
    except LookupError as e:
        raise AdminError("Conta de leitura não encontrada.", 404) from e
    await c.audit(request, "users", "Conta de leitura do banco removida", role)
    return Response(status_code=204)


async def dbaccess_page(request: Request, c: AdminCtx) -> Response:
    users = {u["id"]: u for u in await c.users()}
    rows, dbs, problem = [], [], ""
    try:
        svc = service(request)
        accounts, dbs = await svc.accounts(), await svc.databases()
    except Exception as e:  # the page still explains the feature and the configuration
        accounts, problem = [], f"Banco indisponível agora: {e}"[:200]
    for a in accounts:
        who = users.get(a["owner"])
        rows.append(a | {"owner_name": who["name"] if who else "(removido)", "css": _STATE_CSS[a["state"]],
                         "until_text": a["until"].strftime("%d/%m/%Y") if a["until"] else "—"})  # fmt: skip
    meta = dumps({"roles": [], "apps": [], "dbs": dbs,
                  "users": [{"id": u["id"], "name": u["name"], "email": u["email"]} for u in users.values() if u["active"]]})  # fmt: skip
    return c.render(request, "admin_dbaccess.html", "Acesso", "Acesso ao banco",
                    "Contas só de leitura para dashboards e ferramentas como o DBeaver, por host e porta, com TLS.",
                    rows=rows, conn=conn_info(request), meta=meta, problem=problem, has_dbs=bool(dbs))  # fmt: skip


async def suspend_for(request: Request, uid: str) -> str:
    """The person left (deactivated or every access revoked): their read accounts stop; a database that cannot be
    reached now is said in the audit line instead of failing the whole action."""
    try:
        roles = await service(request).suspend_owner(uid)
    except Exception as e:
        return f"contas de leitura do banco NÃO suspensas ({str(e)[:80]})"
    return f"{len(roles)} conta(s) de leitura do banco suspensa(s)" if roles else ""
