"""Tudo ligado a um app FastAPI: :class:`HubSdk`.

Uso típico::

    sdk = HubSdk.from_env("contratos", app_sql=SCHEMA, handlers=[ImportHandler(...)])
    app = FastAPI(lifespan=sdk.lifespan)
    sdk.install(app)          # idempotência, /_hub/*, x-app-version

Variáveis (injetadas pela plataforma — ver ``docs/CONTRATO-APP.md``): ``HUB_APP_ID``,
``PORT``, ``HUB_DB_URL`` (ou ``HUB_DB_URL_<ID>``), ``HUB_INTERNAL_TOKEN``,
``HUB_SCRATCH_DIR``/``TMPDIR``, ``HUB_HEAVY_MAX``, ``HUB_JOB_LEASE_S``,
``HUB_APP_VERSION_MARKER``, ``HUB_JOBS_MODE``.

Sem banco: outbox e idempotência em memória, sem tarefas duráveis (igual ao Rust).

**Onde as tarefas rodam** (``HUB_JOBS_MODE``, diferença da stack Python medida na onda 7):

- ``inline`` (padrão; ``hub dev`` e testes): o worker de tarefas roda no mesmo processo do
  servidor HTTP. Simples, mas o trabalho de CPU das tarefas (openpyxl, python-calamine,
  formatação) disputa o GIL com as requisições: uma exportação XLSX grande multiplica a
  latência de páginas simples (medido: 4 → 60 ms p50).
- ``process`` (imagem de produção): o processo web não roda tarefas; um **processo de
  tarefas** separado (:meth:`HubSdk.run_worker_process`, iniciado e supervisionado pelo app
  — ex.: ``python -m contratos serve``) as executa. O SIGTERM do web é repassado na hora ao
  de tarefas (:func:`hub_sdk.shutdown.on_process_shutdown`), e um OOM numa importação mata
  só o processo de tarefas (o web continua respondendo; a tarefa retoma do checkpoint).
- ``off``: ninguém executa tarefas neste container (elas ficam na fila).
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

# Prazo de drenagem no desligamento (abaixo dos 10 s do `docker stop`).
DRAIN_S = 8.0
CLEANUP_EVERY_S = 3600.0
# Definida pelo supervisor do processo de tarefas ao religá-lo depois de um SIGKILL.
KILLED_PID_ENV = "HUB_JOBS_KILLED_PID"


def _int_env(name: str) -> int | None:
    try:
        v = int(os.environ.get(name, ""))
    except ValueError:
        return None
    return v if v > 0 else None


def scratch_dir(app_id: str) -> Path:
    """Diretório de trabalho (uploads, relatórios): ``HUB_SCRATCH_DIR`` (``/scratch`` nos
    containers, volume próprio do app) ou o temporário do sistema."""
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
        # Limpeza do app (ex.: arquivos velhos do /scratch): na subida e a cada hora.
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
        # Onda 8: traces (o hub busca em /_hub/telemetry) e flags empurradas pelo hub.
        self.telemetry = Telemetry(app_id)
        self.flags = FlagStore()
        # Onda 9: busca federada e aprovações pela Inbox (defina antes de `install`).
        self.search: SearchFn | None = None
        self.approvals: ApprovalFn | None = None
        # Onda 12 (2.5): verificações extras de prontidão do app (além de banco/encerramento).
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
        """Prontidão (``GET /{app}/_hub/ready``): encerrando = não pronto; com banco,
        migrações aplicadas **e** ``SELECT 1``; depois as verificações de ``ready_checks``."""
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
        """Auditoria fora de transação (exportações, consultas)."""
        await self.outbox.emit(audit(user, action, target, detail))

    def event(self, user: str, action: str, target: str = "", detail: str = "") -> Event:
        return audit(user, action, target, detail)

    # ---- ciclo de vida --------------------------------------------------------------
    @contextlib.asynccontextmanager
    async def lifespan(self, _app: FastAPI) -> AsyncGenerator[None]:
        self.shutdown.chain_signals()
        if self.db is not None:
            await self.db.open("web", jobs_inline=self.jobs_mode == "inline")
            self.outbox = Outbox.postgres(self.db.pool)
            self.idem = Idempotency.postgres(self.db.idem_pool)
            self.jobs = self._new_jobs()
            am = self.after_migrate
            after = (lambda: am(self)) if am is not None else None
            self.db.spawn_migrations(self.app_id, self.app_sql, after)
            if self.handlers and self.jobs_mode == "inline":
                db = self.db
                self.worker = Worker(
                    self.jobs, self.handlers, self.heavy, self.shutdown, ready=lambda: db.ready
                )
                self._bg.append(self.worker.spawn())
            self._bg.append(asyncio.create_task(self._cleanup_loop()))
            self._bg.append(asyncio.create_task(self._job_counts_loop()))
        log.warning("%s: no ar%s", self.app_id, " (pool Postgres do SDK ligado)" if self.db else "")
        try:
            yield
        finally:
            self.shutdown.trigger()
            drained = await self.shutdown.drain(DRAIN_S)
            log.warning(
                "%s: encerrado (%s)",
                self.app_id,
                "tarefas devolvidas à fila" if drained else "tempo esgotado; leases vencem sozinhos",
            )
            for t in self._bg:
                t.cancel()
            for t in self._bg:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            if self.db is not None:
                await self.db.close()

    async def run_worker_process(self) -> None:
        """Processo de tarefas (``HUB_JOBS_MODE=process``): só o worker de tarefas e a
        limpeza periódica, até SIGTERM/SIGINT; drena como o web (8 s)."""
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

        async def on_ready() -> None:
            # O supervisor avisa (HUB_JOBS_KILLED_PID) que o processo anterior morreu por
            # SIGKILL (OOM do cgroup): as tarefas que estavam numa fase de memória viram
            # `failed` com motivo claro, em vez de 5 tentativas que matariam o processo de novo.
            ids = jobs.take_markers(killed)
            if killed is not None and ids:
                failed = await jobs.fail_killed(ids, f"{killed}-", KILLED_BY_MEMORY)
                log.warning(
                    "%s: %d tarefa(s) marcadas como falha após o processo %s ser morto (memória)",
                    self.app_id,
                    len(failed),
                    killed,
                )

        self.worker = Worker(
            jobs, self.handlers, self.heavy, self.shutdown, ready=lambda: db.ready, on_ready=on_ready
        )
        self._bg.append(self.worker.spawn())
        self._bg.append(asyncio.create_task(self._cleanup_loop()))
        log.warning("%s: processo de tarefas no ar (pid %s)", self.app_id, os.getpid())
        await self.shutdown.wait()
        drained = await self.shutdown.drain(DRAIN_S)
        log.warning(
            "%s: processo de tarefas encerrado (%s)",
            self.app_id,
            "tarefas devolvidas à fila" if drained else "tempo esgotado; leases vencem sozinhos",
        )
        for t in self._bg:
            t.cancel()
        for t in self._bg:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        await self.db.close()

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
                # Windows: sem add_signal_handler — signal.signal na thread principal.
                with contextlib.suppress(ValueError, OSError):
                    signal.signal(sig, lambda *_: loop.call_soon_threadsafe(self.shutdown.trigger))

    def _stopping(self) -> bool:
        return self.shutdown.triggered

    async def _cleanup_loop(self) -> None:
        """Limpeza por validade (tabelas do SDK) e os ganchos do app: na subida (assim que
        o banco fica pronto, ou em até 60 s) e depois a cada ``CLEANUP_EVERY_S``."""
        first = True
        while not self.shutdown.triggered:
            waiter = asyncio.create_task(self.shutdown.wait())
            try:
                if first:
                    for _ in range(120):
                        if self.db is None or self.db_ready or self._stopping():
                            break
                        await asyncio.sleep(0.5)
                else:
                    await asyncio.wait({waiter}, timeout=CLEANUP_EVERY_S)
            finally:
                waiter.cancel()
            first = False
            if self._stopping():
                return
            await self.run_cleanup()

    async def _job_counts_loop(self) -> None:
        """Tarefas na fila/rodando (medidor de saturação), a cada 10 s."""
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

    # ---- instalação no app ------------------------------------------------------------
    def install(self, app: FastAPI) -> None:
        """Middleware de idempotência (todas as rotas que alteram dados), endpoints
        internos ``/{id}/_hub/*`` e o cabeçalho ``x-app-version``."""
        app.add_middleware(IdempotencyMiddleware, idem=lambda: self.idem)
        # Idioma da requisição (onda 9): `t()`, números e datas dos fragmentos.
        app.add_middleware(LocaleMiddleware)
        # Por fora da idempotência (o último add_middleware é o mais externo).
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

        # Onda 11: MCP interno gerado do OpenAPI da API do app (`hub_sdk.api.install_api`).
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
            # Prontidão (docs/CONTRATO-APP.md §2): banco configurado e migrações ainda
            # em curso → `x-hub-ready: 0`; o blue/green não troca o tráfego para cá.
            if self.db is not None and not self.db.ready:
                resp.headers["x-hub-ready"] = "0"
            return resp
