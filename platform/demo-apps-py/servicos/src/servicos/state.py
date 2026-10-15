"""Shared state of the routes (one per app, ``app.state.servicos``; handlers get it as a ``StateDep``
parameter) and the helpers every route uses: database errors, audit in the transaction, the session
cookie."""

from __future__ import annotations

import re
import secrets
import time
from pathlib import Path
from typing import Annotated, Any, cast

import asyncpg
from fastapi import Depends, Request
from fastapi.responses import Response
from hub_sdk import outbox
from hub_sdk.app import HubSdk
from hub_sdk.idempotency import idem_ctx
from hub_sdk.identity import identity_of
from hub_sdk.locale import from_headers
from hub_sdk.render import public_base

from servicos import views
from servicos.i18n import personalize
from servicos.session import Sessions
from servicos.ui import notice

HERE = Path(__file__).resolve().parent
COUNT_TTL_S = 15.0
DBERR = (OSError, asyncpg.PostgresError, asyncpg.InterfaceError, TimeoutError)
INT64 = re.compile(r"[+-]?[0-9]+", re.ASCII)


def to_i64(s: str) -> int | None:
    v = int(s) if INT64.fullmatch(s) else None
    return v if v is not None and -(2**63) <= v < 2**63 else None


class State:
    def __init__(self, sdk: HubSdk, scratch: Path, sessions: Sessions) -> None:
        self.sdk = sdk
        self.scratch = scratch
        self.sessions = sessions
        self.templates: dict[str, str] = {}
        self.counts: tuple[float, dict[str, int]] | None = None

    def raw(self, name: str) -> str | None:
        body = self.templates.get(name)
        if body is None:
            try:
                body = (HERE / "fragments" / f"{name}.html").read_text("utf-8")
            except OSError:
                return None
            self.templates[name] = body
        return body

    def skeleton(self, req: Request) -> str | None:
        body = self.raw("_skeleton")
        return None if body is None else personalize(body, user_of(req), from_headers(req.headers))

    def page(self, section: str, req: Request, active: str | None = None) -> str | None:
        """``section`` fragment with the hub context and the "Reports" item in the sidebar
        (``active`` marks the active item of the extra pages)."""
        body = self.raw(section)
        if body is None:
            return None
        body = personalize(body, user_of(req), from_headers(req.headers))
        base = base_of(req)
        return views.inject_nav(body.replace("{{ base }}", base), active or section, base)

    async def status_counts(self) -> dict[str, int]:
        """Count per status (15 s cache: a ``GROUP BY`` over millions of rows on every page
        view would be wasteful)."""
        if self.counts is not None and time.monotonic() - self.counts[0] < COUNT_TTL_S:
            return self.counts[1]
        out: dict[str, int] = {}
        try:
            async with self.sdk.pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT status, count(*) FROM tickets WHERE status IN ('Aberto','Em atendimento','Aguardando') "
                    "GROUP BY status"
                )
            out = {r[0]: r[1] for r in rows}
        except DBERR:
            pass
        self.counts = (time.monotonic(), out)
        return out


def state(req: Request) -> State:
    return cast(State, req.app.state.servicos)


StateDep = Annotated[State, Depends(state)]


def base_of(req: Request) -> str:
    """Public base of the app's links (the hub's ``x-hub-base``, or ``/apps/servicos``)."""
    return public_base(req, "servicos")


def user_of(req: Request) -> str:
    return identity_of(req).display_name


def transient(e: BaseException) -> bool:
    return isinstance(
        e,
        OSError
        | asyncpg.PostgresConnectionError
        | asyncpg.InterfaceError
        | asyncpg.CannotConnectNowError
        | asyncpg.TooManyConnectionsError
        | TimeoutError,
    )


def db_notice(st: State) -> str | None:
    if st.sdk.db is None:
        return notice(
            "info",
            "Banco de dados não configurado",
            "Defina HUB_DB_URL (ou HUB_DB_URL_SERVICOS) para usar os chamados reais. Exibindo os dados de exemplo do protótipo.",
        )
    if not st.sdk.db_ready:
        return notice(
            "warn",
            "Banco de dados indisponível",
            "O banco ainda está subindo ou não respondeu. Tente novamente em instantes.",
        )
    return None


async def audit_tx(conn: Any, req: Request, action: str, target: str, detail: str) -> None:
    """Audit event + idempotency marker in the action's transaction."""
    await outbox.write(conn, outbox.audit(user_of(req), action, target, detail))
    ctx = idem_ctx(req)
    if ctx is not None:
        await ctx.mark_committed(conn)


def session_key(req: Request) -> tuple[str, str | None]:
    """User session: ``svc_sid`` cookie + user from the injected identity. Returns the key and the
    new sid (to write to the cookie) when the browser does not have one yet."""
    user = user_of(req)
    for line in req.headers.getlist("cookie"):
        for part in line.split(";"):
            sid = part.strip().removeprefix("svc_sid=") if part.strip().startswith("svc_sid=") else ""
            if sid and len(sid) <= 64 and sid.isascii() and sid.isalnum():
                return f"{user}|{sid}", None
    sid = secrets.token_hex(16)
    return f"{user}|{sid}", sid


def with_cookie(resp: Response, new_sid: str | None) -> Response:
    if new_sid is not None:
        resp.headers.append("set-cookie", f"svc_sid={new_sid}; Path=/; HttpOnly; SameSite=Lax")
    return resp
