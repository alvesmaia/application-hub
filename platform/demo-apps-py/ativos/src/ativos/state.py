"""Shared state of the routes (one per app, ``app.state.ativos``; handlers get it as a ``StateDep``
parameter) and the helpers every route uses: database errors, audit in the transaction, asset
queries and jobs."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Annotated, Any, cast

import asyncpg
from fastapi import Depends, Request
from hub_sdk import outbox, telemetry
from hub_sdk.app import HubSdk
from hub_sdk.idempotency import idem_ctx
from hub_sdk.identity import identity_of
from hub_sdk.jobs import Job
from hub_sdk.locale import from_headers
from hub_sdk.render import public_base

from ativos.i18n import personalize
from ativos.model import COLS, PAGE_SIZE
from ativos.query import Filter
from ativos.ui import notice

HERE = Path(__file__).resolve().parent
TEXT_COLS = ", ".join(f"{c.key}::text AS {c.key}" for c in COLS)
DB_ERRORS = (OSError, asyncpg.PostgresError)


class State:
    def __init__(self, sdk: HubSdk, scratch: Path) -> None:
        self.sdk = sdk
        self.scratch = scratch
        self.templates: dict[str, str] = {}

    def fragment(self, name: str, req: Request) -> str | None:
        """Static mockup fragment with the hub context (user, language, date, app name, public base)."""
        body = self.templates.get(name)
        if body is None:
            p = HERE / "fragments" / f"{name}.html"
            try:
                body = p.read_text("utf-8")
            except OSError:
                return None
            self.templates[name] = body
        body = body.replace("{{ base }}", base_of(req))
        return personalize(body, user_of(req), from_headers(req.headers))


def state(req: Request) -> State:
    return cast(State, req.app.state.ativos)


StateDep = Annotated[State, Depends(state)]


def base_of(req: Request) -> str:
    """Public base of the app's links/forms for this request (``x-hub-base``, else ``/apps/ativos``)."""
    return public_base(req, "ativos")


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
            "Defina HUB_DB_URL (ou HUB_DB_URL_ATIVOS) para usar o cadastro real. Exibindo os dados de exemplo do protótipo.",
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


async def load_asset(st: State, aid: int) -> Any:
    async with st.sdk.pool.acquire() as c:
        return await c.fetchrow(f"SELECT id, version, {TEXT_COLS} FROM assets WHERE id = $1", aid)


async def list_assets(st: State, f: Filter) -> tuple[int, list[Any]]:
    wh, params = f.where()
    async with st.sdk.pool.acquire() as c:
        # Wave 8: "db …" spans as children of the request span, and traceparent in the SQL comment.
        tc = telemetry.sql_comment()
        async with telemetry.db("SELECT assets (contagem)"):
            total = int(await c.fetchval(f"{tc}SELECT count(*) FROM assets {wh}", *params))
        async with telemetry.db("SELECT assets (página)"):
            rows = await c.fetch(
                f"{tc}SELECT id, tag, name, category, location, status, acquisition_value_cents, acquisition_date "
                f"FROM assets {wh} ORDER BY {f.order()} LIMIT {PAGE_SIZE} OFFSET {(f.page - 1) * PAGE_SIZE}",
                *params,
            )
    return total, list(rows)


def jobs_ready(st: State) -> bool:
    return st.sdk.db is not None and st.sdk.db_ready and st.sdk.jobs is not None


async def get_job(st: State, job_id: str, kind: str) -> Job | None:
    jobs = st.sdk.jobs
    if jobs is None or not jobs_ready(st) or len(job_id) > 80:
        return None
    with contextlib.suppress(OSError, asyncpg.PostgresError):
        j = await jobs.get(job_id)
        if j is not None and j.kind == kind:
            return j
    return None


def values_from(form: Any) -> dict[str, str]:
    return {k: str(v) for k, v in form.items()}


def sent_version(form: dict[str, str]) -> int:
    """The ``version`` the user read (optimistic locking); 0 when missing or not a number."""
    try:
        return int(form.get("version", "0"))
    except ValueError:
        return 0
