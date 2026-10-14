"""Shared state of the Operations routes: stores, engine, jobs and the small helpers every section uses."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from hub.admin.config import resolve
from hub.admin.store import AdminStore
from hub.connections.provision import ObjectStore
from hub.gateway.registry import Registry
from hub.ops import jobs as jb
from hub.ops import model as m
from hub.ops import newapp
from hub.ops import runtime as rt
from hub.ops.backups import Backups
from hub.ops.jobs import Jobs
from hub.ops.model import OpsError, OpsStore, now_ms
from hub.ops.rollout import Rollout


class OpsCtx:
    """One per app (`app.state.ops_ctx`); route handlers get it with `Depends(ctx)`."""

    def __init__(
        self,
        app: FastAPI,
        templates: Jinja2Templates,
        page_ctx: Callable[[Request], dict[str, Any]],
        icon: Callable[[str], str],
        client: httpx.AsyncClient,
        data_dir: Path,
        engine: rt.Engine | None = None,
        objstore: ObjectStore | None = None,
    ) -> None:
        self.app, self.templates, self.page_ctx, self.icon = app, templates, page_ctx, icon
        self.client, self.data_dir = client, data_dir
        self.admin = cast(AdminStore, app.state.admin)
        self.registry = cast(Registry, app.state.gateway.registry)
        self.store = OpsStore(data_dir, self.registry.ids(), self.admin.vault)
        self.eng, self.jobs = engine or rt.Engine(), Jobs()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.backups = Backups(
            self.store, self.eng, objstore or ObjectStore(), data_dir, self.app_env, self.hook
        )
        self.rollout = Rollout(self.eng, self.registry, client, self.app_env, self.env_vars)
        self.rollout.fresh = lambda a: newapp.base_container() if newapp.is_dynamic(data_dir, a) else None

    # ---- helpers used by every section ------------------------------------------------------------
    def name_of(self, a: str) -> str:
        e = self.registry.get(a)
        return e.name if e else a

    def need_app(self, a: str) -> Any:
        e = self.registry.get(a)
        if e is None:
            raise OpsError("Aplicação não encontrada.", 404)
        return e

    @staticmethod
    def actor_of(r: Request) -> str:
        return str(r.state.user["name"])

    def log(self, level: str, app_id: str, actor: str, msg: str) -> None:
        self.admin.logs.add(level, app_id, actor, msg, "ops")

    def spawn(self, coro: Any) -> None:
        """Background job that outlives the request; a failure is printed, never swallowed silently."""
        t = asyncio.ensure_future(coro)
        self.tasks.add(t)
        t.add_done_callback(
            lambda f: (
                f.cancelled()
                or f.exception() is None
                or print("ops: tarefa falhou:", repr(f.exception()), flush=True)
            )
        )
        t.add_done_callback(self.tasks.discard)

    @staticmethod
    async def body(request: Request) -> dict[str, Any]:
        try:
            v = await request.json()
        except ValueError as e:
            raise OpsError("Dados inválidos: verifique os campos enviados.") from e
        if not isinstance(v, dict):
            raise OpsError("Dados inválidos: verifique os campos enviados.")
        return v

    # ---- app configuration (variables, backup hooks, approved scripts) ----------------------------
    async def app_env(self, app_id: str, env: str) -> dict[str, str]:
        data = await self.admin.cfg.read()
        return {r["name"]: r["value"] for r in resolve(data["env"], app_id, env)}

    async def env_vars(self) -> list[dict[str, Any]]:
        return list((await self.admin.cfg.read())["env"])

    async def hook(self, app_id: str, which: str) -> None:
        """Backup hook of the app (`manifest.backup.pre|post`), called with the app's internal token."""
        e = self.registry.get(app_id)
        path = (((e.manifest.model_extra or {}).get("backup") or {}) if e and e.manifest else {}).get(which)
        token = self.admin.tokens.get(app_id)
        if e and path and token:
            r = await self.client.post(
                f"{e.upstream.rstrip('/')}{path}", headers={jb.TOKEN_HEADER: token}, timeout=30
            )
            if r.status_code >= 300:
                raise OpsError(f"hook {which} do app respondeu {r.status_code}", 502)

    def active_scripts(self, app_id: str, staging: bool) -> list[tuple[str, str]]:
        """The app's own approved scripts of the ACTIVE version in the environment: only these go into the CSP and `hub.js`."""
        d, env = self.store.read(), m.STAGING if staging else m.PRODUCAO
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

    async def reconcile(self) -> None:
        """On startup: a deployment left in progress (replica dead for >10 min) becomes a failure."""
        async with self.store.locked(True) as d:
            for x in d["deployments"]:
                if x["status"] == m.IMPLANTANDO and now_ms() - x["at"] > 600_000:
                    x.update(status=m.FALHOU, note="interrompida: o hub reiniciou durante a implantação")


def tone(s: str) -> str:
    def has(xs: list[str]) -> bool:
        return any(x in s for x in xs)

    if has(["Implantado", "Concluído", "Aprovado", "Em execução", "Válido", "Concluída"]):
        return "status-pill ops-pill-ok"
    if has(["Validando", "Implantando", "Em andamento", "Aguardando", "Na fila", "Cancelando"]):
        return "status-pill ops-pill-warn"
    if has(["Rejeitado", "Falhou", "Parado", "Tentativas esgotadas"]):
        return "status-pill ops-pill-err"
    return "status-pill ops-pill-muted"


def br(v: float, places: int) -> str:
    return f"{v:.{places}f}".replace(".", ",")


def env_key(v: str | None) -> str:
    return "staging" if v == "staging" else "producao"


def rollout_env(label: str) -> str:
    return "staging" if label in (m.STAGING, "staging") else "producao"


def ctx(request: Request) -> OpsCtx:
    """FastAPI dependency: the Operations state that `mount_ops` stored on the app."""
    return cast(OpsCtx, request.app.state.ops_ctx)
