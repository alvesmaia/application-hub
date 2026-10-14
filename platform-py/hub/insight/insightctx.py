"""Shared state of the insight routes (Logs, Audit, Health, Flags, Analytics, Usage) and their small helpers."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, cast

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from hub.admin.config import PERMISSIONS, AdminError
from hub.admin.routes import dumps
from hub.admin.store import AdminStore
from hub.insight.runtime import Insight
from hub.ux.texts import page_response


class InsightCtx:
    """One per app (`app.state.insight_ctx`); route handlers get it with `Depends(ctx)`."""

    def __init__(
        self,
        app: FastAPI,
        templates: Jinja2Templates,
        page_ctx: Callable[[Request], dict[str, Any]],
        icon: Callable[[str], str],
        insight: Insight,
        client: httpx.AsyncClient,
    ) -> None:
        self.app, self.templates, self.page_ctx, self.icon = app, templates, page_ctx, icon
        self.insight, self.client = insight, client
        self.store = cast(AdminStore, app.state.admin)
        self.ops, self.tel, self.an, self.us = insight.ops, insight.tel, insight.analytics, insight.usage

    def refs(self) -> list[dict[str, str]]:
        return cast(list[dict[str, str]], self.app.state.app_refs())

    def short(self, app_id: str) -> str:
        return next((a["short"] for a in self.refs() if a["id"] == app_id), "Plataforma")

    def meta_json(self) -> str:
        apps = [{k: a[k] for k in ("color", "id", "ink", "name", "short")} for a in self.refs()]
        return dumps({"apps": apps, "permissions": list(PERMISSIONS)})

    def app_ids_meta(self) -> dict[str, Any]:
        return {"apps": [{"id": a["id"], "short": a["short"]} for a in self.refs()]}

    def render(
        self, request: Request, name: str, eyebrow: str, title: str, subtitle: str, tab: str = "", **ctx: Any
    ) -> Response:
        data = self.page_ctx(request) | {
            "icon": self.icon,
            "eyebrow": eyebrow,
            "title": title,
            "subtitle": subtitle,
            "tab": tab or title,
        }
        return page_response(self.templates, request, name, data | ctx)

    @staticmethod
    def actor_of(request: Request) -> str:
        return str(request.state.user["name"])

    @staticmethod
    async def body(request: Request) -> dict[str, Any]:
        try:
            data = await request.json()
        except ValueError as e:
            raise AdminError("Dados inválidos: verifique os campos enviados.") from e
        if not isinstance(data, dict):
            raise AdminError("Dados inválidos: verifique os campos enviados.")
        return data

    @staticmethod
    def need_settings(request: Request) -> None:
        if "editar_configuracoes" not in request.state.permissions:
            raise AdminError("Alterar o analytics exige “Editar configurações da plataforma”.", 403)

    async def trail(self) -> list[dict[str, Any]]:
        return list(reversed((await self.store.cfg.read()).get("audit", [])))

    def usage_of(self, request: Request) -> dict[str, Any]:
        """The "Platform usage (30 days)" card (the same as in Settings)."""
        sm = self.an.summary(int(time.time() * 1000))
        names = {a["id"]: a["short"] for a in self.refs()}
        top = sorted(((names.get(r[0], r[0]), r[3], r[4]) for r in sm["apps"]), key=lambda x: -x[2])[:5]
        return {"mau": sm["mau"], "apps": top, "zero": sm["searches_zero"], "searches": sm["searches"]}


def ctx(request: Request) -> InsightCtx:
    """FastAPI dependency: the insight state that `mount_insight` stored on the app."""
    return cast(InsightCtx, request.app.state.insight_ctx)
