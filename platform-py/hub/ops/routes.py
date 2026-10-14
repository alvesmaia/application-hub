"""Operations: wires the shared state (`OpsCtx`) and mounts the section modules (`routes_*`) (F3c).

F5b: blue/green (`rollout.py`), staging, supervision (`supervisor.py`) and database provisioning (`provision.py`)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates

from hub.connections.provision import ObjectStore
from hub.gateway.csp import set_script_provider
from hub.ops import routes_backups, routes_deploys, routes_pages, routes_runtime
from hub.ops import runtime as rt
from hub.ops.leader import Leader
from hub.ops.model import OpsError, OpsStore
from hub.ops.opsctx import OpsCtx, br, env_key, rollout_env, tone  # noqa: F401 (re-exported)
from hub.ux.texts import tr


async def guard(request: Request) -> None:
    """Writes to `/api/ops/*` only as JSON (packages: multipart + `X-Hub-Request: 1`)."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    ct = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if request.url.path == "/api/ops/packages":
        if ct != "multipart/form-data":
            raise OpsError("Envie o pacote como multipart/form-data (campo package).", 415)
        if request.headers.get("x-hub-request") != "1":
            raise OpsError("Header X-Hub-Request: 1 obrigatório no envio de pacotes.")
    elif ct != "application/json":
        raise OpsError("Envie os dados como application/json.", 415)


async def no_store(request: Request, call_next: Callable[[Request], Any]) -> Response:
    resp = cast(Response, await call_next(request))
    if request.url.path.startswith("/api/ops/"):
        resp.headers["cache-control"] = "no-store"
    return resp


async def ops_error(request: Request, e: Exception) -> Response:
    err = cast(OpsError, e)
    msg = tr(err.message, getattr(request.state, "lang", "pt-BR"))
    return JSONResponse({"error": msg}, status_code=err.status, headers={"cache-control": "no-store"})


def mount_ops(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
    client: httpx.AsyncClient,
    data_dir: Path,
    *,
    engine: rt.Engine | None = None,
    objstore: ObjectStore | None = None,
) -> OpsStore:
    c = OpsCtx(app, templates, page_ctx, icon, client, data_dir, engine, objstore)
    app.state.ops_ctx = c
    app.state.ops, app.state.ops_jobs, app.state.ops_engine = c.store, c.jobs, c.eng
    app.state.ops_backups, app.state.ops_rollout, app.state.ops_reconcile = c.backups, c.rollout, c.reconcile
    app.state.leader = Leader(None)  # replaced by the one from the `lifespan` (Background.start)
    set_script_provider(c.active_scripts)
    app.add_exception_handler(OpsError, ops_error)
    app.middleware("http")(no_store)
    api = APIRouter(prefix="/api/ops", dependencies=[Depends(guard)])
    for section in (routes_deploys, routes_runtime, routes_backups):
        api.include_router(section.router)
        section.mount(c)
    app.include_router(api)
    routes_pages.mount(c)
    app.include_router(routes_pages.router)
    return c.store
