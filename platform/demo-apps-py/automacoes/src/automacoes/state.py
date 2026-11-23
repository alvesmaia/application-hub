"""Shared state of the routes (``app.state.automacoes``; handlers take it as ``StateDep``) and the
request helpers every route uses: role-aware renderer, database notice, admin guard, PRG
redirect, idempotency marker and run creation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, cast

import asyncpg
from fastapi import Depends, Request
from fastapi.responses import Response
from hub_sdk.app import HubSdk
from hub_sdk.idempotency import idem_ctx
from hub_sdk.identity import identity_of
from hub_sdk.locale import t
from hub_sdk.render import Renderer, public_base
from markupsafe import Markup
from starlette.requests import HTTPConnection

from automacoes import store
from automacoes.access import is_admin, login_of
from automacoes.model import Process, Run

HERE = Path(__file__).resolve().parent
APP_ID = "automacoes"
DB_ERRORS = (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError)


class State:
    def __init__(self, sdk: HubSdk, user_render: Renderer, admin_render: Renderer) -> None:
        self.sdk = sdk
        self.user_render = user_render
        self.admin_render = admin_render

    @property
    def pool(self) -> Any:
        return self.sdk.pool

    def db_ok(self) -> bool:
        return self.sdk.db_ready

    def render_for(self, req: HTTPConnection) -> Renderer:
        """Sidebar by role: admins also get Processos, Pacotes and Gatilhos."""
        return self.admin_render if is_admin(req) else self.user_render

    @property
    def render(self) -> Renderer:
        return self.user_render

    def page(self, req: Request, template: str, active: str, status: int = 200, **ctx: Any) -> Response:
        ctx.setdefault("notice_html", self.db_notice())
        return self.render_for(req).page(req, template, active, status, admin=is_admin(req), **ctx)

    def fragment(self, req: Request, template: str, status: int = 200, **ctx: Any) -> Response:
        return self.render.fragment(template, status, base=public_base(req, APP_ID), **ctx)

    def db_notice(self) -> Markup | None:
        if self.sdk.db is None:
            return self.render.notice(
                "info",
                t("Banco de dados não configurado"),
                t("Defina HUB_DB_URL para guardar pacotes, processos e execuções."),
            )
        if not self.sdk.db_ready:
            return self.render.notice(
                "warn",
                t("Banco de dados indisponível"),
                t("O banco ainda está subindo ou não respondeu. Tente novamente em instantes."),
            )
        return None

    def forbidden(self, req: Request) -> Response:
        n = self.render.notice(
            "error", t("Acesso restrito"), t("Esta área é só para administradores do app.")
        )
        return self.page(req, "aviso.html", "catalogo", 403, notice_html=n)


def state(conn: HTTPConnection) -> State:
    return cast(State, conn.app.state.automacoes)


StateDep = Annotated[State, Depends(state)]


def user_of(req: HTTPConnection) -> str:
    return identity_of(req).display_name


def requester(req: HTTPConnection) -> store.Requester:
    return store.Requester(user_of(req), login_of(req))


def see_other(path: str) -> Response:
    """PRG: ``303`` to the app's own ``/automacoes/…`` path (the gateway rewrites it to the
    public URL, or keeps it a fragment for hub.js)."""
    return Response(status_code=303, headers={"location": f"/{APP_ID}{path}", "cache-control": "no-store"})


async def mark_committed(conn: Any, req: Request) -> None:
    """Idempotency marker in the action's transaction (a retried submission never runs twice)."""
    ctx = idem_ctx(req)
    if ctx is not None:
        await ctx.mark_committed(conn)


async def new_run(st: State, req: Request, p: Process, params: dict[str, Any], trigger: str) -> Run:
    """Creates the run (row + job + audit + idempotency marker in one transaction) and wakes the worker."""
    async with st.pool.acquire() as c, c.transaction():
        run = await store.create_run(c, p, params, trigger, requester(req))
        await mark_committed(c, req)
    st.sdk.wake()
    return run


def to_int(s: str) -> int | None:
    """Positive id from a path/query value (anything else → ``None``)."""
    s = s.strip()
    return int(s) if s.isdigit() and len(s) < 18 else None


def may_see(req: HTTPConnection, run: Run) -> bool:
    return is_admin(req) or run.requested_login == login_of(req)
