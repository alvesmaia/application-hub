"""Administration › Users, Roles and permissions and Environment variables: pages and `/api/admin/*` APIs.

Wires the shared state (`AdminCtx`) and mounts the section modules (`routes_*`, `tokens`)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates

from hub.admin import routes_env, routes_roles, routes_users, tokens
from hub.admin.adminctx import AdminCtx, err, json_only
from hub.admin.adminctx import actor_of as actor_of
from hub.admin.adminctx import admin_level as admin_level
from hub.admin.adminctx import bound_summary as bound_summary
from hub.admin.adminctx import dumps as dumps
from hub.admin.adminctx import initials as initials
from hub.admin.adminctx import json_or_empty as json_or_empty
from hub.admin.adminctx import relative as relative
from hub.admin.adminctx import same_roles as same_roles
from hub.admin.config import AdminError
from hub.gateway.registry import Registry
from hub.ux.texts import tr

# Same order as before the split: FastAPI matches routes in registration order.
_SECTIONS = (routes_users, routes_roles, routes_env, tokens)


async def admin_error(request: Request, exc: Exception) -> JSONResponse:
    e = cast(AdminError, exc)
    return err(e.status, tr(str(e), getattr(request.state, "lang", "pt-BR")))


def mount_admin(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
    registry: Registry,
    refresh_roles: Callable[[], None],
) -> None:
    c = AdminCtx(app, templates, page_ctx, icon, registry, refresh_roles)
    app.state.admin_ctx = c
    app.add_exception_handler(AdminError, admin_error)
    api = APIRouter(prefix="/api/admin", dependencies=[Depends(json_only)])
    for section in _SECTIONS:
        api.include_router(section.api)
        app.include_router(section.pages)
    app.include_router(api)
    app.state.app_refs = c.refs
