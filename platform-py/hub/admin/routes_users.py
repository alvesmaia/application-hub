"""Administration › Users: page `/admin/users` and `/api/admin/users*`."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from hub.admin.adminctx import (
    AdminCtx,
    PasswordIn,
    UserIn,
    UserPatch,
    body,
    check_grant,
    check_roles,
    check_target,
    ctx,
    dumps,
    initials,
    parse,
    relative,
    same_roles,
    wrap,
)
from hub.admin.config import AdminError
from hub.admin.routes_roles import roles_page
from hub.auth import now_ms, user_view
from hub.ux.pager import paginate

api = APIRouter()
pages = APIRouter()


@api.get("/users")
async def list_users(c: AdminCtx = Depends(ctx)) -> Any:
    return [await c.view_of(u) for u in await c.users()]


@api.post("/users", status_code=201)
async def create_user(request: Request, c: AdminCtx = Depends(ctx)) -> Any:
    i = parse(UserIn, await body(request))
    known = await c.known_roles()
    check_roles(known, i.roles)
    check_grant(request, known, i.roles)
    try:
        u = await c.auth.create_user(i.email, i.name, i.roles, i.password, i.must_change_password)
    except (ValueError, LookupError) as e:
        raise wrap(e) from e
    await c.audit(request, "users", "Usuário criado", f"{u['name']} ({u['email']}) · {', '.join(u['roles'])}")
    return await c.view_of(u)


def _check_update(
    request: Request, known: list[dict[str, Any]], p: UserPatch, before: dict[str, Any]
) -> None:
    me = request.state.user.get("id") == before["id"]
    # Nobody changes their own roles or deactivates themselves (not even the Administrator).
    if me and ((p.roles is not None and not same_roles(p.roles, before["roles"])) or p.active is False):
        raise AdminError("Você não pode alterar as próprias funções nem desativar a própria conta.", 403)
    if not me:
        check_target(request, known, before)
    if p.roles is not None:
        check_grant(request, known, p.roles)


def _changes(before: dict[str, Any], u: dict[str, Any]) -> list[str]:
    changes = []
    if before["name"] != u["name"]:
        changes.append(f"nome: {u['name']}")
    if before["roles"] != u["roles"]:
        changes.append(f"funções: {', '.join(u['roles'])}")
    if before["active"] != u["active"]:
        changes.append("reativado" if u["active"] else "desativado (sessões encerradas)")
    return changes


@api.put("/users/{user_id}")
async def update_user(request: Request, user_id: str, c: AdminCtx = Depends(ctx)) -> Any:
    p = parse(UserPatch, await body(request))
    known = await c.known_roles()
    if p.roles is not None:
        check_roles(known, p.roles)
    before = await c.user(user_id)
    if before is None:
        raise AdminError("Usuário não encontrado.", 404)
    _check_update(request, known, p, before)
    try:
        u = await c.auth.update_user(user_id, name=p.name, roles=p.roles, active=p.active)
    except (ValueError, LookupError) as e:
        raise wrap(e) from e
    if changes := _changes(before, u):
        await c.audit(request, "users", "Usuário alterado", f"{u['email']} · {' · '.join(changes)}")
    return await c.view_of(u)


@api.delete("/users/{user_id}", status_code=204)
async def delete_user(request: Request, user_id: str, c: AdminCtx = Depends(ctx)) -> Response:
    if request.state.user.get("id") == user_id:
        raise AdminError("Você não pode excluir a própria conta.", 403)
    before = await c.user(user_id)
    if before is None:
        raise AdminError("Usuário não encontrado.", 404)
    check_target(request, await c.known_roles(), before)
    try:
        u = await c.auth.delete_user(user_id)
    except (ValueError, LookupError) as e:
        raise wrap(e) from e
    await c.audit(request, "users", "Usuário excluído", u["email"])
    return Response(status_code=204)


@api.post("/users/{user_id}/reset-password")
async def reset_password(request: Request, user_id: str, c: AdminCtx = Depends(ctx)) -> Any:
    i = parse(PasswordIn, await body(request))
    if request.state.user.get("id") == user_id:
        raise AdminError("Para trocar a sua própria senha use Alterar senha no menu do usuário.", 403)
    target = await c.user(user_id)
    if target is None:
        raise AdminError("Usuário não encontrado.", 404)
    check_target(request, await c.known_roles(), target)
    try:
        u = await c.auth.reset_password(user_id, i.password)
    except (ValueError, LookupError) as e:
        raise wrap(e) from e
    await c.audit(
        request,
        "users",
        "Senha redefinida",
        f"{u['email']} · senha provisória (troca obrigatória no próximo acesso)",
    )
    return await c.view_of(u)


@api.post("/users/{user_id}/unlock")
async def unlock(request: Request, user_id: str, c: AdminCtx = Depends(ctx)) -> Any:
    try:
        u = await c.auth.unlock(user_id)
    except LookupError as e:
        raise wrap(e) from e
    if c.auth.db:
        await c.auth.db.rate_like("login", f"acct:%|{u['email']}", 0, 0, clear=True)
    failures = c.app.state.login_failures
    for k in [k for k in failures if k.startswith("acct:") and k.endswith(f"|{u['email']}")]:
        del failures[k]
    await c.audit(request, "users", "Usuário desbloqueado", u["email"])
    return await c.view_of(u)


def _status(u: dict[str, Any], v: dict[str, Any]) -> tuple[str, str]:
    if not u["active"]:
        return "Desativado", "status-pill status-draft"
    if v["locked"]:
        return "Bloqueado", "status-pill status-maintenance"
    if v["must_change_password"]:
        return "Troca de senha pendente", "status-pill status-pending"
    return "Ativo", "status-pill status-active"


def _user_row(u: dict[str, Any], v: dict[str, Any], now: int, me: Any, grants: list[str]) -> dict[str, Any]:
    status, cls = _status(u, v)
    return {
        "item": json.dumps(v, ensure_ascii=False, separators=(",", ":")),
        "name": u["name"],
        "initials": initials(u["name"]),
        "email": u["email"],
        "roles": ", ".join(u["roles"]),
        "status": status,
        "status_class": cls,
        "last": relative(u["last_login"], now) if u.get("last_login") else "Nunca",
        "locked": v["locked"],
        "active": u["active"],
        "id": u["id"],
        "sso": v["sso"],
        "grants": ", ".join(grants),
        "me": u["id"] == me,
    }


@pages.get("/admin/users", response_class=HTMLResponse)
async def users_page(request: Request, tab: str = "", c: AdminCtx = Depends(ctx)) -> Response:
    if tab == "roles":
        return await roles_page(request, c)
    users = sorted(await c.users(), key=lambda u: u["name"].lower())
    now, me = now_ms(), request.state.user.get("id")
    inbox = request.app.state.inbox
    rows = []
    for u in users:
        v = user_view(u, now, await c.locked_now(u["email"]))
        rows.append(_user_row(u, v, now, me, inbox.grants_for(u["id"])))
    meta = dumps(
        {
            "apps": [{"id": a["id"], "name": a["name"]} for a in c.refs()],
            "roles": [r["name"] for r in await c.known_roles()],
        }
    )
    total = len(rows)
    rows, pager = paginate(request, rows)
    return c.render(
        request,
        "admin_users.html",
        "Acesso",
        "Usuários",
        "Contas com acesso à plataforma, suas funções e o estado de cada acesso.",
        rows=rows,
        total=total,
        meta=meta,
        pager=pager,
    )
