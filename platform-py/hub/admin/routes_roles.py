"""Administration › Roles and permissions: tab `/admin/users?tab=roles` and `/api/admin/roles*`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from hub.admin.adminctx import AdminCtx, AppsIn, GrantIn, actor_of, body, ctx, dumps, is_super, parse
from hub.admin.config import ADMIN_ROLE, PERMISSIONS, AdminError

api = APIRouter()
pages = APIRouter()


async def role_change(request: Request, c: AdminCtx, fn: Callable[..., Any], *args: Any) -> Any:
    if not is_super(request):
        raise AdminError("Só o Administrador altera funções e permissões.", 403)
    out = await fn(actor_of(request), *args)
    c.refresh_roles()
    return out


@api.get("/roles")
async def list_roles(c: AdminCtx = Depends(ctx)) -> Any:
    return await c.store.cfg.roles()


@api.post("/roles", status_code=201)
async def create_role(request: Request, c: AdminCtx = Depends(ctx)) -> Any:
    return await role_change(request, c, c.store.cfg.role_create, await body(request))


@api.put("/roles/{name}")
async def update_role(request: Request, name: str, c: AdminCtx = Depends(ctx)) -> Any:
    return await role_change(request, c, c.store.cfg.role_update, name, await body(request))


@api.delete("/roles/{name}", status_code=204)
async def delete_role(request: Request, name: str, c: AdminCtx = Depends(ctx)) -> Response:
    await role_change(request, c, c.store.cfg.role_delete, name)
    return Response(status_code=204)


@api.put("/roles/{name}/permissions/{index}")
async def set_permission(request: Request, name: str, index: str, c: AdminCtx = Depends(ctx)) -> Any:
    if not index.isdigit():
        raise AdminError("Permissão inválida.")
    g = parse(GrantIn, await body(request))
    return await role_change(request, c, c.store.cfg.role_permission, name, int(index), g.granted)


@api.put("/roles/{name}/apps")
async def set_role_apps(request: Request, name: str, c: AdminCtx = Depends(ctx)) -> Any:
    a = parse(AppsIn, await body(request))
    return await role_change(request, c, c.store.cfg.role_apps, name, a.apps)


@pages.get("/admin/roles")
async def old_roles() -> Response:
    return RedirectResponse("/admin/users?tab=roles", 308)


def _role_row(r: dict[str, Any], n: int, editable: bool, refs: list[dict[str, str]]) -> dict[str, Any]:
    perms, apps = r.get("permissions", []), r.get("apps")
    return {
        "name": r["name"],
        "description": r["description"],
        "members": f"{n} usuário{'' if n == 1 else 's'}",
        "level": r["level"],
        "level_class": {
            "Total": "status-pill status-active",
            "Alto": "status-pill status-pending",
        }.get(r["level"], "status-pill status-draft"),
        "locked": r["name"] == ADMIN_ROLE or not editable,
        "item": dumps(
            {
                "name": r["name"],
                "description": r["description"],
                "members": r.get("members", 0),
                "level": r["level"],
            }
        ),
        "menu": f"role:{r['name']}",
        "perms": [
            (p, bool(perms[i]) if i < len(perms) else False, str(i)) for i, p in enumerate(PERMISSIONS)
        ],
        "all_apps": apps is None,
        "apps": [(a["id"], a["name"], apps is None or a["id"] in apps) for a in refs],
    }


async def roles_page(request: Request, c: AdminCtx) -> Response:
    editable = is_super(request)
    users = await c.users()
    count = lambda n: sum(1 for u in users if any(r.lower() == n.lower() for r in u["roles"]))  # noqa: E731
    roles = [_role_row(r, count(r["name"]), editable, c.refs()) for r in await c.store.cfg.roles()]
    subtitle = (
        "Clique em uma função para ver e ajustar as permissões concedidas."
        if editable
        else "Só o Administrador altera funções e permissões; aqui você pode consultá-las."
    )
    return c.render(
        request,
        "admin_roles.html",
        "Acesso",
        "Funções e permissões",
        subtitle,
        roles=roles,
        editable=editable,
        meta=c.meta_json(),
    )
