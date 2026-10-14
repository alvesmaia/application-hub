"""Shared state of the routes (one per app, ``app.state.fornecedores``); handlers get it as a
``StateDep`` parameter."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import Depends, Request
from hub_sdk import storage
from hub_sdk.app import HubSdk
from hub_sdk.identity import identity_of
from hub_sdk.locale import from_headers

from fornecedores.i18n import personalize
from fornecedores.lookup import Lookup

HERE = Path(__file__).resolve().parent


class State:
    def __init__(self, sdk: HubSdk, scratch: Path, store: storage.Connection | None, lookup: Lookup) -> None:
        self.sdk = sdk
        self.scratch = scratch
        self.store = store
        self.lookup = lookup
        self.templates: dict[str, str] = {}
        self.tasks: set[asyncio.Task[Any]] = set()

    def fragment(self, name: str, req: Request) -> str | None:
        """Mockup fragment with the hub context (user, language, date, app name)."""
        body = self.templates.get(name)
        if body is None:
            try:
                body = (HERE / "fragments" / f"{name}.html").read_text("utf-8")
            except OSError:
                return None
            self.templates[name] = body
        return personalize(body, user_of(req), from_headers(req.headers))

    def audit(self, req: Request, action: str, target: str, detail: str) -> None:
        """Outbox audit without delaying the response (there is no transaction to share)."""
        t = asyncio.create_task(self.sdk.emit(user_of(req), action, target, detail))
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    def user_store(self, req: Request) -> storage.Connection | None:
        if self.store is None:
            return None
        return self.store.for_user(req.headers.get("x-hub-user"), req.headers.get("x-hub-user-sig"))


def user_of(req: Request) -> str:
    return identity_of(req).display_name


def state(req: Request) -> State:
    return cast(State, req.app.state.fornecedores)


StateDep = Annotated[State, Depends(state)]
