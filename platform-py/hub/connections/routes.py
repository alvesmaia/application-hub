"""Administration › Connections: page, `/api/admin/connections*` API and «Provision storage».

Wires the shared state (`ConnCtx`) and mounts the API module (`routes_api`)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from hub.admin.routes import dumps, err, json_or_empty
from hub.admin.store import AdminStore
from hub.auth import now_ms
from hub.connections import provision as prov
from hub.connections import routes_api
from hub.connections.conn_view import connection_row, storage_rows
from hub.connections.connctx import ConnCtx, ctx
from hub.connections.connctx import broker_url as broker_url
from hub.connections.store import ConnError, ConnStore
from hub.storage.graph import Endpoints
from hub.ux.texts import page_response, t

pages = APIRouter()


async def conn_error(request: Request, exc: Exception) -> JSONResponse:
    e = cast(ConnError, exc)
    return err(e.status, str(e))


def mount_connections(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
    store: ConnStore,
    admin: AdminStore,
    objects: prov.ObjectStore,
) -> None:
    app.state.conn_ctx = ConnCtx(app, templates, page_ctx, icon, store, admin, objects)

    async def storage_for_deploy(app_id: str, env: str) -> dict[str, Any]:
        """F22: `requires.storage` in the deployment (same flow as «Provision storage»)."""
        _, out = await prov.provision(store, objects, app_id, env, "implantação", broker_url())
        return out

    app.state.storage_provision = storage_for_deploy
    app.add_exception_handler(ConnError, conn_error)
    app.state.stepup_fails = {}
    app.include_router(
        routes_api.router, prefix="/api/admin/connections", dependencies=[Depends(json_or_empty)]
    )
    app.include_router(pages)


@pages.get("/admin/connections", response_class=HTMLResponse)
async def connections_page(request: Request, c: ConnCtx = Depends(ctx)) -> Response:
    lang = request.state.lang
    tr_ = lambda k: t(k, lang)  # noqa: E731
    apps = c.app.state.app_refs()
    by_id = {a["id"]: a for a in apps}
    now = now_ms()
    rows = [connection_row(x, by_id, tr_, now, lang) for x in c.store.all()]
    pending = await c.admin.cfg.pending_restart()
    ep = Endpoints.from_env()
    data = c.page_ctx(request) | {
        "icon": c.icon,
        "eyebrow": "Aplicações",
        "title": tr_("conn.title"),
        "subtitle": tr_("conn.subtitle"),
        "rows": rows,
        "pending_apps": [
            (a["id"], a["name"], len(pending[a["id"]]["vars"])) for a in apps if a["id"] in pending
        ],
        "storage": storage_rows(c.store.storage, by_id, tr_),
        "app_opts": [(a["id"], a["name"]) for a in apps],
        "meta": dumps({"apps": [{"id": a["id"], "short": a["short"]} for a in apps]}),
        "sim_warning": None if ep.is_default() else f"{tr_('conn.sim_warning')} ({ep.login} · {ep.graph})",
        "broker_off": not c.app.state.broker_enabled,
    }
    return page_response(c.templates, request, "admin_connections.html", data)
