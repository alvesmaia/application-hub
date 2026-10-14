"""Operations: wires the shared state (`OpsCtx`) and mounts the section modules (`routes_*`) (F3c).

F5b: blue/green (`rollout.py`), staging, supervision (`supervisor.py`) and database provisioning (`provision.py`)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates

from hub.admin.config import resolve
from hub.admin.store import AdminStore
from hub.connections.provision import ObjectStore
from hub.gateway.csp import set_script_provider
from hub.gateway.registry import Registry
from hub.ops import jobs as jb
from hub.ops import model as m
from hub.ops import newapp
from hub.ops import runtime as rt
from hub.ops.backups import Backups
from hub.ops.jobs import Jobs
from hub.ops.leader import Leader
from hub.ops.model import OpsError, OpsStore, now_ms
from hub.ops.opsctx import OpsCtx, br, env_key, rollout_env, tone  # noqa: F401 (re-exported)
from hub.ops.rollout import Rollout
from hub.ops.routes_backups import mount_backups
from hub.ops.routes_deploys import mount_deploys
from hub.ops.routes_pages import mount_pages
from hub.ops.routes_runtime import mount_runtime
from hub.ux.texts import tr


def mount_ops(  # noqa: C901, PLR0915
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
    admin = cast(AdminStore, app.state.admin)
    registry = cast(Registry, app.state.gateway.registry)
    store = OpsStore(data_dir, registry.ids(), admin.vault)
    eng, jobs = engine or rt.Engine(), Jobs()
    tasks: set[asyncio.Task[Any]] = set()
    app.state.ops, app.state.ops_jobs, app.state.ops_engine = store, jobs, eng

    def active_scripts(app_id: str, staging: bool) -> list[tuple[str, str]]:
        """The app's own approved scripts of the ACTIVE version in the environment: only these go into the CSP and `hub.js`."""
        d, env = store.read(), m.STAGING if staging else m.PRODUCAO
        dep = next(
            (
                x
                for x in d["deployments"]
                if x["app"] == app_id and x["env"] == env and x["status"] == m.IMPLANTADO
            ),
            None,
        )
        if dep is None:
            return []
        pending = {s["path"] for s in m.pending_scripts(d, dep)}
        return [
            (s["path"].removeprefix("ui/assets/"), s["sha384"])
            for s in dep.get("scripts", [])
            if s["path"] not in pending
        ]

    set_script_provider(active_scripts)
    app.state.leader = Leader(None)  # replaced by the one from the `lifespan` (Background.start)

    async def app_env(app_id: str, env: str) -> dict[str, str]:
        data = await admin.cfg.read()
        return {r["name"]: r["value"] for r in resolve(data["env"], app_id, env)}

    async def hook(app_id: str, which: str) -> None:
        e = registry.get(app_id)
        path = (((e.manifest.model_extra or {}).get("backup") or {}) if e and e.manifest else {}).get(which)
        token = admin.tokens.get(app_id)
        if e and path and token:
            r = await client.post(
                f"{e.upstream.rstrip('/')}{path}", headers={jb.TOKEN_HEADER: token}, timeout=30
            )
            if r.status_code >= 300:
                raise OpsError(f"hook {which} do app respondeu {r.status_code}", 502)

    backups = Backups(store, eng, objstore or ObjectStore(), data_dir, app_env, hook)
    app.state.ops_backups = backups

    async def env_vars() -> list[dict[str, Any]]:
        return list((await admin.cfg.read())["env"])

    rollout = Rollout(eng, registry, client, app_env, env_vars)
    rollout.fresh = lambda a: newapp.base_container() if newapp.is_dynamic(data_dir, a) else None
    app.state.ops_rollout = rollout

    async def reconcile() -> None:
        """On startup: a deployment left in progress (replica dead for >10 min) becomes a failure."""
        async with store.locked(True) as d:
            for x in d["deployments"]:
                if x["status"] == m.IMPLANTANDO and now_ms() - x["at"] > 600_000:
                    x.update(status=m.FALHOU, note="interrompida: o hub reiniciou durante a implantação")

    app.state.ops_reconcile = reconcile

    def name_of(a: str) -> str:
        e = registry.get(a)
        return e.name if e else a

    def spawn(coro: Any) -> None:
        t = asyncio.ensure_future(coro)
        tasks.add(t)
        t.add_done_callback(
            lambda f: (
                f.cancelled()
                or f.exception() is None
                or print("ops: tarefa falhou:", repr(f.exception()), flush=True)
            )
        )
        t.add_done_callback(tasks.discard)

    @app.exception_handler(OpsError)
    async def ops_error(request: Request, e: OpsError) -> JSONResponse:
        msg = tr(e.message, getattr(request.state, "lang", "pt-BR"))
        return JSONResponse({"error": msg}, status_code=e.status, headers={"cache-control": "no-store"})

    def actor_of(r: Request) -> str:
        return str(r.state.user["name"])

    def log(level: str, app_id: str, actor: str, msg: str) -> None:
        admin.logs.add(level, app_id, actor, msg, "ops")

    def need_app(a: str) -> Any:
        e = registry.get(a)
        if e is None:
            raise OpsError("Aplicação não encontrada.", 404)
        return e

    async def guard(request: Request) -> None:
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

    api = APIRouter(prefix="/api/ops", dependencies=[Depends(guard)])

    @app.middleware("http")
    async def no_store(request: Request, call_next: Callable[[Request], Any]) -> Response:
        resp = cast(Response, await call_next(request))
        if request.url.path.startswith("/api/ops/"):
            resp.headers["cache-control"] = "no-store"
        return resp

    async def body(request: Request) -> dict[str, Any]:
        try:
            v = await request.json()
        except ValueError as e:
            raise OpsError("Dados inválidos: verifique os campos enviados.") from e
        if not isinstance(v, dict):
            raise OpsError("Dados inválidos: verifique os campos enviados.")
        return v

    c = OpsCtx(
        app=app,
        templates=templates,
        page_ctx=page_ctx,
        icon=icon,
        client=client,
        data_dir=data_dir,
        admin=admin,
        registry=registry,
        store=store,
        eng=eng,
        jobs=jobs,
        backups=backups,
        rollout=rollout,
        api=api,
        name_of=name_of,
        spawn=spawn,
        actor_of=actor_of,
        log=log,
        need_app=need_app,
        body=body,
    )
    mount_deploys(c)
    runtime_state, list_tasks = mount_runtime(c)
    mount_backups(c)
    app.include_router(api)

    mount_pages(c, runtime_state, list_tasks)
    return store
