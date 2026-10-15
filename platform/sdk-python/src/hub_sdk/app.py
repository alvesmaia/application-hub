"""Everything wired to a FastAPI app: :class:`HubSdk`.

Typical usage::

    sdk = HubSdk.from_env("contratos", app_sql=SCHEMA, handlers=[ImportHandler(...)])
    app = FastAPI(lifespan=sdk.lifespan)
    sdk.install(app)          # idempotency, /_hub/*, x-app-version

Variables (injected by the platform — see ``docs/CONTRATO-APP.md``): ``HUB_APP_ID``,
``PORT``, ``HUB_DB_URL`` (or ``HUB_DB_URL_<ID>``), ``HUB_INTERNAL_TOKEN``,
``HUB_SCRATCH_DIR``/``TMPDIR``, ``HUB_HEAVY_MAX``, ``HUB_JOB_LEASE_S``,
``HUB_APP_VERSION_MARKER``, ``HUB_JOBS_MODE``.

No DB: outbox and idempotency in memory, no durable jobs.

**Where jobs run** (``HUB_JOBS_MODE``, Python stack difference measured in wave 7):

- ``inline`` (default; ``hub dev`` and tests): the jobs worker runs in the same process as the
  HTTP server. Simple, but the jobs' CPU work (openpyxl, python-calamine,
  formatting) contends for the GIL with the requests: a large XLSX export multiplies the
  latency of simple pages (measured: 4 → 60 ms p50).
- ``process`` (production image): the web process runs no jobs; a separate **jobs process**
  (:meth:`HubSdk.run_worker_process`, started and supervised by the app
  — e.g. ``python -m contratos serve``) runs them. The web's SIGTERM is forwarded immediately to the
  jobs process (:func:`hub_sdk.shutdown.on_process_shutdown`), and an OOM during an import kills
  only the jobs process (the web keeps responding; the job resumes from the checkpoint).
- ``off``: nobody runs jobs in this container (they stay in the queue).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from hub_sdk import internal, mcp
from hub_sdk import ready as _ready
from hub_sdk.approvals import ApprovalFn
from hub_sdk.db import Database, app_env
from hub_sdk.flags import FlagStore
from hub_sdk.heavy import Heavy
from hub_sdk.idempotency import Idempotency, IdempotencyMiddleware
from hub_sdk.jobs import KILLED_BY_MEMORY, Handler, Jobs, Worker
from hub_sdk.locale import LocaleMiddleware
from hub_sdk.outbox import Event, Outbox, audit
from hub_sdk.schema import cleanup
from hub_sdk.search import SearchFn
from hub_sdk.shutdown import Shutdown
from hub_sdk.telemetry import Telemetry, TraceMiddleware

log = logging.getLogger("hub_sdk")

# Shutdown drain deadline (below the 10 s of `docker stop`).
DRAIN_S = 8.0
CLEANUP_EVERY_S = 3600.0
# Set by the jobs-process supervisor when it restarts it after a SIGKILL.
KILLED_PID_ENV = "HUB_JOBS_KILLED_PID"


def _int_env(name: str) -> int | None:
    try:
        v = int(os.environ.get(name, ""))
    except ValueError:
        return None
    return v if v > 0 else None


def scratch_dir(app_id: str) -> Path:
    """Working directory (uploads, reports): ``HUB_SCRATCH_DIR`` (``/scratch`` in containers, the app's
    own volume) or the system temp directory."""
    base = os.environ.get("HUB_SCRATCH_DIR") or os.environ.get("TMPDIR") or None
    import tempfile

    d = Path(base or tempfile.gettempdir()) / f"hub-{app_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


class HubSdk:
    def __init__(
        self,
        app_id: str,
        db_url: str | None,
        app_sql: str = "",
        handlers: Sequence[Handler] = (),
        heavy: Heavy | None = None,
        token: str | None = None,
        after_migrate: Callable[[HubSdk], Awaitable[None]] | None = None,
        jobs_mode: str = "inline",
        cleanup_hooks: Sequence[Callable[[HubSdk], Awaitable[None]]] = (),
    ) -> None:
        self.app_id = app_id
        # App cleanup (e.g. old files in /scratch): at startup and every hour.
        self.cleanup_hooks = list(cleanup_hooks)
        self.jobs_mode = jobs_mode if jobs_mode in ("inline", "process", "off") else "inline"
        self.app_sql = app_sql
        self.handlers = list(handlers)
        self.heavy = heavy or Heavy.from_env()
        self.token = token
        self.shutdown = Shutdown()
        self.db: Database | None = Database(db_url) if db_url is not None and db_url else None
        self.outbox = Outbox.memory()
        self.idem = Idempotency.memory()
        self.jobs: Jobs | None = None
        self.worker: Worker | None = None
        self.after_migrate = after_migrate
        self._bg: list[asyncio.Task[Any]] = []
        self.version = os.environ.get("HUB_APP_VERSION_MARKER", "").strip() or "dev"
        self._init_extensions()

    def _init_extensions(self) -> None:
        # Wave 8: traces (the hub fetches them at /_hub/telemetry) and flags pushed by the hub.
        self.telemetry = Telemetry(self.app_id)
        self.flags = FlagStore()
        # Wave 9: federated search and Inbox approvals (set before `install`).
        self.search: SearchFn | None = None
        self.approvals: ApprovalFn | None = None
        # Wave 12 (2.5): extra app readiness checks (beyond DB/shutdown).
        self.ready_checks: list[tuple[str, _ready.Check]] = []
        self._job_counts: tuple[float, float] | None = None
        heavy_ = self.heavy

        def gauges() -> dict[str, float]:
            g = {"heavy_in_use": float(heavy_.in_use), "heavy_max": float(heavy_.max)}
            if self._job_counts is not None:
                g["jobs_queued"], g["jobs_running"] = self._job_counts
            return g

        self.telemetry.set_gauges(gauges)

    @classmethod
    def from_env(
        cls,
        app_id: str | None = None,
        app_sql: str = "",
        handlers: Sequence[Handler] = (),
        after_migrate: Callable[[HubSdk], Awaitable[None]] | None = None,
        cleanup_hooks: Sequence[Callable[[HubSdk], Awaitable[None]]] = (),
    ) -> HubSdk:
        aid = (app_id or os.environ.get("HUB_APP_ID", "")).strip()
        if not aid:
            raise RuntimeError("HUB_APP_ID não definido")
        return cls(
            aid,
            app_env(aid, "HUB_DB_URL"),
            app_sql=app_sql,
            handlers=handlers,
            token=internal.token_from_env(),
            after_migrate=after_migrate,
            jobs_mode=os.environ.get("HUB_JOBS_MODE", "inline").strip().lower(),
            cleanup_hooks=cleanup_hooks,
        )

    # ---- estado -------------------------------------------------------------------
    @property
    def pool(self) -> Any:
        return self.db.pool if self.db else None

    @property
    def db_ready(self) -> bool:
        return self.db is not None and self.db.ready

    def readiness(self) -> _ready.Readiness:
        """Readiness (``GET /{app}/_hub/ready``): shutting down = not ready; with DB,
        migrations applied **and** ``SELECT 1``; then the ``ready_checks`` checks."""
        sd = self.shutdown
        r = _ready.Readiness().flag("shutdown", "encerrando (SIGTERM)", lambda: not sd.triggered)
        db = self.db
        if db is not None:

            async def _db() -> None:
                if not db.ready:
                    raise _ready.NotReady("migrações do banco em curso")
                try:
                    async with db.pool.acquire() as c:
                        await c.execute("SELECT 1")
                except Exception as e:
                    raise _ready.NotReady(f"banco não respondeu ({type(e).__name__})") from e

            r.check("db", _db)
        for name, fn in self.ready_checks:
            r.check(name, fn)
        return r

    def wake(self) -> None:
        if self.worker is not None:
            self.worker.wake()

    async def emit(self, user: str, action: str, target: str = "", detail: str = "") -> None:
        """Audit outside a transaction (exports, queries)."""
        await self.outbox.emit(audit(user, action, target, detail))

    def event(self, user: str, action: str, target: str = "", detail: str = "") -> Event:
        return audit(user, action, target, detail)

    # ---- lifecycle ----------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def lifespan(self, _app: FastAPI) -> AsyncGenerator[None]:
        self.shutdown.chain_signals()
        if self.db is not None:
            await self._start_web(self.db)
        log.warning("%s: no ar%s", self.app_id, " (pool Postgres do SDK ligado)" if self.db else "")
        try:
            yield
        finally:
            self.shutdown.trigger()
            await self._drain_and_stop("encerrado")
            if self.db is not None:
                await self.db.close()

    async def _start_web(self, db: Database) -> None:
        await db.open("web", jobs_inline=self.jobs_mode == "inline")
        self.outbox = Outbox.postgres(db.pool)
        self.idem = Idempotency.postgres(db.idem_pool)
        jobs = self._new_jobs()
        self.jobs = jobs
        am = self.after_migrate
        after = (lambda: am(self)) if am is not None else None
        db.spawn_migrations(self.app_id, self.app_sql, after)
        if self.handlers and self.jobs_mode == "inline":
            self.worker = Worker(jobs, self.handlers, self.heavy, self.shutdown, ready=lambda: db.ready)
            self._bg.append(self.worker.spawn())
        self._bg.append(asyncio.create_task(self._cleanup_loop()))
        self._bg.append(asyncio.create_task(self._job_counts_loop()))

    async def _drain_and_stop(self, what: str) -> None:
        """Drains in-flight work (``DRAIN_S``), logs the outcome and stops the background tasks."""
        drained = await self.shutdown.drain(DRAIN_S)
        log.warning(
            "%s: %s (%s)",
            self.app_id,
            what,
            "tarefas devolvidas à fila" if drained else "tempo esgotado; leases vencem sozinhos",
        )
        for t in self._bg:
            t.cancel()
        for t in self._bg:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def run_worker_process(self) -> None:
        """Jobs process (``HUB_JOBS_MODE=process``): only the jobs worker and the
        periodic cleanup, until SIGTERM/SIGINT; drains like the web (8 s)."""
        self._install_signals()
        if self.db is None or not self.handlers:
            log.warning(
                "%s: processo de tarefas sem banco ou sem tarefas; aguardando o desligamento", self.app_id
            )
            await self.shutdown.wait()
            return
        await self.db.open("worker")
        self.outbox = Outbox.postgres(self.db.pool)
        jobs = self._new_jobs()
        self.jobs = jobs
        self.db.spawn_migrations(self.app_id, self.app_sql, None)
        db = self.db
        killed = _int_env(KILLED_PID_ENV)
        self.worker = Worker(
            jobs,
            self.handlers,
            self.heavy,
            self.shutdown,
            ready=lambda: db.ready,
            on_ready=lambda: self._fail_killed_jobs(jobs, killed),
        )
        self._bg.append(self.worker.spawn())
        self._bg.append(asyncio.create_task(self._cleanup_loop()))
        log.warning("%s: processo de tarefas no ar (pid %s)", self.app_id, os.getpid())
        await self.shutdown.wait()
        await self._drain_and_stop("processo de tarefas encerrado")
        await db.close()

    async def _fail_killed_jobs(self, jobs: Jobs, killed: int | None) -> None:
        # The supervisor notifies (HUB_JOBS_KILLED_PID) that the previous process died from
        # SIGKILL (cgroup OOM): the jobs that were in a memory phase become
        # `failed` with a clear reason, instead of 5 attempts that would kill the process again.
        ids = jobs.take_markers(killed)
        if killed is not None and ids:
            failed = await jobs.fail_killed(ids, f"{killed}-", KILLED_BY_MEMORY)
            log.warning(
                "%s: %d tarefa(s) marcadas como falha após o processo %s ser morto (memória)",
                self.app_id,
                len(failed),
                killed,
            )

    def _new_jobs(self) -> Jobs:
        assert self.db is not None  # noqa: S101 -- startup initializes database before job construction
        return Jobs(
            self.db.jobs_pool,
            handlers=self.handlers,
            marker_dir=scratch_dir(self.app_id) / ".hub-jobs-mem",
        )

    def _install_signals(self) -> None:
        import signal

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.shutdown.trigger)
            except (NotImplementedError, RuntimeError, ValueError):
                # Windows: no add_signal_handler — signal.signal on the main thread.
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(sig, lambda *_: loop.call_soon_threadsafe(self.shutdown.trigger))

    def _stopping(self) -> bool:
        return self.shutdown.triggered

    async def _cleanup_loop(self) -> None:
        """Cleanup by expiry (SDK tables) and the app hooks: at startup (as soon as
        the DB is ready, or within 60 s) and then every ``CLEANUP_EVERY_S``."""
        first = True
        while not self.shutdown.triggered:
            await self._wait_next_cleanup(first)
            first = False
            if self._stopping():
                return
            await self.run_cleanup()

    async def _wait_next_cleanup(self, first: bool) -> None:
        waiter = asyncio.create_task(self.shutdown.wait())
        try:
            if first:
                await self._wait_db_ready()
            else:
                await asyncio.wait({waiter}, timeout=CLEANUP_EVERY_S)
        finally:
            waiter.cancel()

    async def _wait_db_ready(self) -> None:
        for _ in range(120):
            if self.db is None or self.db_ready or self._stopping():
                return
            await asyncio.sleep(0.5)

    async def _job_counts_loop(self) -> None:
        """Jobs queued/running (saturation gauge), every 10 s."""
        while not self.shutdown.triggered:
            await asyncio.sleep(10)
            if not self.db_ready:
                continue
            try:
                rows = await self.pool.fetch(
                    "SELECT status, count(*) AS n FROM hub_jobs WHERE status IN ('queued','running') GROUP BY status"
                )
                d = {r["status"]: float(r["n"]) for r in rows}
                self._job_counts = (d.get("queued", 0.0), d.get("running", 0.0))
            except Exception as e:
                log.debug("contagem de tarefas: %s", e)

    async def run_cleanup(self) -> None:
        if self.db_ready:
            try:
                await cleanup(self.pool)
            except Exception as e:
                log.warning("limpeza por validade: %s", e)
        for hook in self.cleanup_hooks:
            try:
                await hook(self)
            except Exception as e:
                log.warning("limpeza do app: %s", e)

    # ---- app installation ------------------------------------------------------------
    def install(self, app: FastAPI) -> None:
        """Idempotency middleware (all routes that change data), internal endpoints
        ``/{id}/_hub/*`` and the ``x-app-version`` header."""
        app.add_middleware(IdempotencyMiddleware, idem=lambda: self.idem)
        # Request language (wave 9): `t()`, numbers and dates of the fragments.
        app.add_middleware(LocaleMiddleware)
        # Outside idempotency (the last add_middleware is the outermost).
        app.add_middleware(TraceMiddleware, telemetry=lambda: self.telemetry)
        app.include_router(
            internal.router(
                self.app_id,
                self.token,
                lambda: self.outbox,
                lambda: self.jobs,
                self.wake,
                telemetry=self.telemetry,
                flags=self.flags,
                search=self.search,
                approvals=self.approvals,
            )
        )

        # Wave 11: internal MCP generated from the app's API OpenAPI (`hub_sdk.api.install_api`).
        def _doc() -> dict[str, Any]:
            f = getattr(app.state, "hub_api_doc", None)
            return f() if callable(f) else {"paths": {}}

        app.include_router(_ready.router(self.app_id, self.readiness))
        app.include_router(mcp.router(self.app_id, self.token, lambda: app, _doc, self.version))
        version = self.version

        @app.middleware("http")
        async def _version(request: Any, call_next: Any) -> Any:
            resp = await call_next(request)
            resp.headers["x-app-version"] = version
            # Readiness (docs/CONTRATO-APP.md §2): DB configured and migrations still
            # in progress → `x-hub-ready: 0`; blue/green does not switch traffic here.
            if self.db is not None and not self.db.ready:
                resp.headers["x-hub-ready"] = "0"
            return resp
