"""Shared state of the Connections routes (stores, object store) and the helpers every handler uses."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from hub.admin.routes import actor_of
from hub.admin.store import AdminStore
from hub.auth import AuthStore, now_ms, verify
from hub.connections import provision as prov
from hub.connections.store import ConnError, ConnStore

BAD = "Dados inválidos: verifique os campos enviados."


def broker_url() -> str:
    """Address of the broker that the apps use (`HUB_BROKER_PUBLIC_URL`)."""
    return os.getenv("HUB_BROKER_PUBLIC_URL", "").strip() or "http://hub-web:8081"


class ConnCtx:
    """One per app (`app.state.conn_ctx`); route handlers get it with `Depends(ctx)`."""

    def __init__(
        self,
        app: FastAPI,
        templates: Jinja2Templates,
        page_ctx: Callable[[Request], dict[str, Any]],
        icon: Callable[[str], str],
        store: ConnStore,
        admin: AdminStore,
        objects: prov.ObjectStore,
    ) -> None:
        self.app, self.templates, self.page_ctx, self.icon = app, templates, page_ctx, icon
        self.store, self.admin, self.objects = store, admin, objects
        self.auth = cast(AuthStore, app.state.auth)

    @staticmethod
    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError as e:
            raise ConnError(BAD) from e
        if not isinstance(data, dict):
            raise ConnError(BAD)
        return data

    async def audit(
        self, request_or_actor: Request | str, icon_: str, title: str, detail: str, scope: str = "platform"
    ) -> None:
        who = request_or_actor if isinstance(request_or_actor, str) else actor_of(request_or_actor)
        await self.admin.audit(who, title, detail, icon=icon_, scope=scope)

    async def sync(self, who: str) -> None:
        await self.store.sync_env(who, broker_url())

    @staticmethod
    def permits(request: Request, c: dict[str, Any]) -> bool:
        """A token restricted to apps only touches connections linked exclusively to its apps."""
        allowed = (request.state.token or {}).get("apps") or []
        return not allowed or (bool(c["bindings"]) and all(b["app"] in allowed for b in c["bindings"]))

    @staticmethod
    def env_key(env: str) -> str:
        if env in ("", "producao", "production"):
            return "producao"
        if env == "staging":
            return "staging"
        raise ConnError("Ambiente inválido (use producao ou staging).")

    def step_up_for(self, request: Request) -> Callable[[str], Any]:
        async def check(password: str) -> str | None:
            u = request.state.user
            if request.state.token is not None or not u.get("id"):
                return "Apagar um bucket com objetos exige reautenticação com senha numa sessão do navegador (tokens e agentes não confirmam)."
            tries = self.app.state.stepup_fails.setdefault(u["id"], [])
            cut = now_ms() - 15 * 60_000
            tries[:] = [x for x in tries if x > cut]
            if len(tries) >= 5:
                return "Muitas tentativas. Aguarde alguns minutos e tente de novo."
            async with self.auth.locked() as d:
                row = next((x for x in d["users"] if x["id"] == u["id"]), None)
            if password and row and verify(password, row.get("password_hash")):
                return None
            tries.append(now_ms())
            return "Senha incorreta: a remoção do bucket com objetos não foi confirmada."

        return check


def ctx(request: Request) -> ConnCtx:
    """FastAPI dependency: the Connections state that `mount_connections` stored on the app."""
    return cast(ConnCtx, request.app.state.conn_ctx)
