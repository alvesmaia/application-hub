"""Shared state of the routes (one per app, ``app.state.coletor``; handlers get it as a ``StateDep``
parameter) and the request helpers every route uses."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, cast

import asyncpg
from fastapi import Depends
from hub_sdk.app import HubSdk
from hub_sdk.identity import identity_of
from hub_sdk.locale import t
from hub_sdk.render import Renderer
from markupsafe import Markup
from starlette.requests import HTTPConnection

HERE = Path(__file__).resolve().parent
DB_ERRORS = (OSError, asyncpg.PostgresError)


class State:
    def __init__(self, sdk: HubSdk, render: Renderer) -> None:
        self.sdk = sdk
        self.render = render


def state(conn: HTTPConnection) -> State:
    # HTTPConnection (not Request): the WebSocket route takes the same dependency.
    return cast(State, conn.app.state.coletor)


StateDep = Annotated[State, Depends(state)]


def db_notice(st: State) -> Markup | None:
    if st.sdk.db is None:
        return st.render.notice(
            "info", t("Banco de dados não configurado"), t("Defina HUB_DB_URL para usar o cadastro real.")
        )
    if not st.sdk.db_ready:
        return st.render.notice(
            "warn",
            t("Banco de dados indisponível"),
            t("O banco ainda está subindo ou não respondeu. Tente novamente."),
        )
    return None


def user_login(req: HTTPConnection) -> str:
    return identity_of(req).user or "anonimo"


def user_name(req: HTTPConnection) -> str:
    return identity_of(req).display_name


def public_url() -> str:
    """Public address of the hub (the "open on the phone" QR of the test sheet)."""
    return os.environ.get("COLETOR_PUBLIC_URL", "").strip().rstrip("/")
