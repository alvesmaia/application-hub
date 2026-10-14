"""Postgres pool (asyncpg) with the codecs the SDK expects, and background migration.

- ``HUB_DB_URL`` (or ``HUB_DB_URL_<ID>``): the app's database URL, injected by the platform.
- Pools separados (mesma URL): handlers (``HUB_DB_POOL``, 12),
  idempotency (``HUB_IDEM_POOL``, 12) and jobs (``HUB_JOBS_POOL``, 5) — a burst of
  requests does not stall the lease heartbeat nor block itself.
- **Connection budget**: the app's role has ``CONNECTION LIMIT 40`` on hub-db. The
  default sizes are **per process**, so (measured in wave 7: 2 uvicorn processes plus the jobs one exceeded
  40 and 12.6% of the requests failed with *too many connections*):
  with ``UVICORN_WORKERS=N`` the web defaults are divided by N; in
  ``HUB_JOBS_MODE=process`` mode the web uses 2 job connections (it only enqueues/queries) and the
  jobs process opens only handlers (4) + jobs (5), with no idempotency pool.
  Default: web 12 + 12 + 2 (+ jobs 4 + 5) = 35 ≤ 40. Explicit variables take precedence.

``json``/``jsonb`` come in and go out as Python objects (codec registered on each connection):
every connection passed to the SDK must come from :func:`create_pool`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import asyncpg

from hub_sdk.schema import migrate

log = logging.getLogger("hub_sdk.db")


def app_env(app_id: str, name: str) -> str | None:
    """``NAME_<ID>`` with fallback to ``NAME`` (empty = absent)."""
    for k in (f"{name}_{app_id.upper().replace('-', '_')}", name):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    return None


async def _init(conn: Any) -> None:
    for t in ("json", "jsonb"):
        await conn.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(url: str, size: int) -> Any:
    """Lazy pool (``min_size=0``): the app starts even with the DB down."""
    return await asyncpg.create_pool(
        dsn=url,
        min_size=0,
        max_size=max(1, size),
        init=_init,
        command_timeout=None,
        max_inactive_connection_lifetime=300,
    )


def pool_size(var: str, default: int, per_web_process: bool = False) -> int:
    """``var`` from the environment; otherwise ``default`` — divided among the web processes
    (``UVICORN_WORKERS``) when ``per_web_process``."""
    raw = os.environ.get(var, "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    if per_web_process:
        try:
            n = max(1, int(os.environ.get("UVICORN_WORKERS", "1") or 1))
        except ValueError:
            n = 1
        return max(2, default // n)
    return default


class Database:
    """Pools + "ready" state (migrations applied)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.pool: Any = None
        self.idem_pool: Any = None
        self.jobs_pool: Any = None
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    async def open(self, role: str = "web", jobs_inline: bool = True) -> None:
        """``role="web"``: handlers + idempotency + jobs (2 if the jobs run in another
        process); ``role="worker"``: only handlers (4) and jobs (5)."""
        if role == "worker":
            self.pool = await create_pool(self.url, pool_size("HUB_WORKER_DB_POOL", 4))
            self.jobs_pool = await create_pool(self.url, pool_size("HUB_JOBS_POOL", 5))
            return
        self.pool = await create_pool(self.url, pool_size("HUB_DB_POOL", 12, per_web_process=True))
        self.idem_pool = await create_pool(self.url, pool_size("HUB_IDEM_POOL", 12, per_web_process=True))
        jobs = pool_size("HUB_JOBS_POOL", 5, per_web_process=True) if jobs_inline else 2
        self.jobs_pool = await create_pool(self.url, jobs)

    def spawn_migrations(self, app_id: str, app_sql: str, after: Any = None) -> None:
        """Migrates in the background until it succeeds (does not hold up startup nor the health
        check); ``after`` (optional coroutine) runs afterwards — e.g. the initial load."""

        async def loop() -> None:
            wait = 0.5
            while True:
                try:
                    async with self.pool.acquire() as c:
                        await migrate(c, app_sql)
                    log.warning("%s: banco pronto (migrações aplicadas)", app_id)
                    self._ready.set()
                    break
                except Exception as e:  # DB down, network, credential: retry
                    log.warning("%s: banco indisponível (%s); nova tentativa em %.1f s", app_id, e, wait)
                    await asyncio.sleep(wait)
                    wait = min(wait * 2, 10.0)
            if after is not None:
                try:
                    await after()
                except Exception:
                    log.exception("%s: pós-migração falhou", app_id)

        self._task = asyncio.create_task(loop())

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        for p in (self.pool, self.idem_pool, self.jobs_pool):
            if p is not None:
                try:
                    await asyncio.wait_for(p.close(), 5)
                except (TimeoutError, OSError):
                    p.terminate()


# --- Portable SQL layer (SQLAlchemy Core + Alembic) -------------------------------------------
# New apps (the `python-fastapi` template of `hub project create`): the same code runs on SQLite in
# `hub dev` and on Postgres in the hub. URL from ``DATABASE_URL`` (fallback ``HUB_DB_URL``). Extra ``hub-sdk[sql]``.


def database_url(default: str = "sqlite:///.hub/dev.db") -> str:
    """SQLAlchemy URL of the app's DB: ``DATABASE_URL`` > ``HUB_DB_URL`` > ``default``
    (``postgres(ql)://`` becomes ``postgresql+psycopg://``, the driver of the ``sql`` extra)."""
    url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("HUB_DB_URL", "").strip() or default
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def engine(url: str | None = None) -> Any:
    """Synchronous SQLAlchemy Core engine. On SQLite it creates the file's folder."""
    import sqlalchemy as sa

    return sa.create_engine(_sqlite_dir(url or database_url()), pool_pre_ping=True)


def _sqlite_dir(u: str) -> str:
    from pathlib import Path

    if u.startswith("sqlite:///") and ":memory:" not in u:
        Path(u.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return u


def upgrade(migrations_dir: str | os.PathLike[str], url: str | None = None, revision: str = "head") -> None:
    """Applies the Alembic migrations of ``migrations_dir`` (folder with ``env.py`` and ``versions/``)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", _sqlite_dir(url or database_url()).replace("%", "%%"))
    command.upgrade(cfg, revision)
