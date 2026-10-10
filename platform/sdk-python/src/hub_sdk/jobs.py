"""Tarefas duráveis na tabela ``hub_jobs`` — mesma tabela, SQL e estados do SDK Rust.

Ciclo de vida: ``queued`` → ``running`` → ``done`` | ``failed`` (erro definitivo) |
``dead`` (tentativas esgotadas: dead-letter) | ``cancelled``. Falha transitória volta a
``queued`` com ``run_after`` = agora + :func:`backoff` (exponencial com *equal jitter*).
:meth:`Jobs.retry` devolve uma tarefa terminal à fila mantendo o checkpoint.

- **Assumir** (:meth:`Jobs.claim`): ``UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP
  LOCKED LIMIT 1)`` sobre ``queued`` vencidas ou ``running`` com **lease vencido**.
- **Fence por tentativa**: cada claim grava um ``attempt_token`` novo; heartbeat,
  checkpoint, progresso e fim só valem com o token da tentativa atual.
- **Lease**: renovado a cada ``lease/3`` e a cada checkpoint; sem renovar por um lease
  inteiro (banco fora), o worker para.
- **Checkpoint** (:meth:`JobCtx.save_checkpoint`): gravado com a conexão/transação do
  lote — lote e checkpoint confirmam juntos ou nenhum.
- **Tempo máximo** por tentativa; **cancelar**; **evento final** no outbox na mesma
  transação da mudança de estado.

Diferença documentada para o Rust: o trabalho de CPU de um handler Python deve ir para
``asyncio.to_thread`` (o laço de eventos precisa continuar livre para o heartbeat e o
HTTP); o semáforo :class:`~hub_sdk.heavy.Heavy` é por processo.
"""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import inspect
import logging
import os
import random
import time
from collections.abc import Awaitable, Callable, Generator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from hub_sdk import outbox
from hub_sdk.heavy import Heavy
from hub_sdk.schema import new_id
from hub_sdk.shutdown import Shutdown

log = logging.getLogger("hub_sdk.jobs")

STATUSES = ("queued", "running", "done", "failed", "dead", "cancelled")
TERMINAL = ("done", "failed", "dead", "cancelled")
LABELS = {
    "queued": "Na fila",
    "running": "Em execução",
    "done": "Concluída",
    "failed": "Falhou",
    "dead": "Tentativas esgotadas",
    "cancelled": "Cancelada",
}
MAX_LIST = 500


def label(status: str) -> str:
    return LABELS.get(status, "Desconhecido")


def _lease_default() -> float:
    try:
        v = int(os.environ.get("HUB_JOB_LEASE_S", "30"))
    except ValueError:
        v = 30
    return float(v if v >= 3 else 30)


@dataclass(frozen=True)
class JobSpec:
    """Contrato de um tipo de tarefa (espelha ``manifest.jobs``)."""

    kind: str
    max_attempts: int = 3
    max_duration: float = 3600.0
    lease: float = field(default_factory=_lease_default)
    backoff_base: float = 2.0
    backoff_max: float = 300.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "max_attempts", max(1, self.max_attempts))
        object.__setattr__(self, "lease", max(1.0, self.lease))


def backoff(attempt: int, base: float, max_: float, jitter: float) -> float:
    """Espera antes da tentativa ``attempt + 1``: ``base · 2^(attempt-1)`` limitada a
    ``max_``, metade fixa + metade aleatória (``jitter`` ∈ [0, 1))."""
    exp = min(max(attempt, 1), 30) - 1
    d = min(base * (1 << min(exp, 20)), max_)
    half = d / 2
    return half + half * min(max(jitter, 0.0), 0.999999)


@dataclass
class Job:
    id: str
    kind: str
    status: str
    payload: Any
    attempts: int
    max_attempts: int
    max_duration_s: int
    checkpoint: Any
    progress_done: int
    progress_total: int
    progress_text: str
    result: Any
    error: str | None
    cancel_requested: bool
    user: str
    created_at: int
    updated_at: int
    finished_at: int | None
    elapsed_s: float
    run_after: int
    attempt_token: str | None = None

    def to_json(self) -> dict[str, Any]:
        """JSON do ``GET /_hub/jobs`` (sem o ``attempt_token``, que nunca sai do app)."""
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "attempt_token"}
        return d

    @property
    def percent(self) -> int:
        if self.status == "done":
            return 100
        if self.progress_total <= 0:
            return 0
        return max(0, min(100, self.progress_done * 100 // self.progress_total))


COLS = (
    "id, kind, status, payload, attempts, max_attempts, max_duration_s, checkpoint, progress_done, "
    "progress_total, progress_text, result, error, cancel_requested, user_name, "
    "(extract(epoch FROM created_at) * 1000)::int8 AS created_ms, "
    "(extract(epoch FROM updated_at) * 1000)::int8 AS updated_ms, "
    "(extract(epoch FROM finished_at) * 1000)::int8 AS finished_ms, "
    "extract(epoch FROM coalesce(finished_at, now()) - created_at)::float8 AS elapsed, "
    "(extract(epoch FROM run_after) * 1000)::int8 AS run_after_ms, attempt_token"
)


def job_of(r: Any) -> Job:
    status = r["status"]
    return Job(
        id=r["id"],
        kind=r["kind"],
        status=status if status in STATUSES else "failed",
        payload=r["payload"],
        attempts=r["attempts"],
        max_attempts=r["max_attempts"],
        max_duration_s=r["max_duration_s"],
        checkpoint=r["checkpoint"],
        progress_done=r["progress_done"],
        progress_total=r["progress_total"],
        progress_text=r["progress_text"],
        result=r["result"],
        error=r["error"],
        cancel_requested=r["cancel_requested"],
        user=r["user_name"],
        created_at=r["created_ms"],
        updated_at=r["updated_ms"],
        finished_at=r["finished_ms"],
        elapsed_s=float(r["elapsed"]),
        run_after=r["run_after_ms"],
        attempt_token=r["attempt_token"],
    )


class JobError(Exception):
    """Resultado de uma tentativa que não concluiu."""


class JobRetry(JobError):
    """Falha transitória: nova tentativa com backoff (ou ``dead`` se esgotou)."""


class JobFail(JobError):
    """Falha definitiva (entrada inválida etc.): ``failed``, sem novas tentativas."""


class JobCancelled(JobError):
    """Cancelamento pedido."""


class JobLostLease(JobError):
    """Outro worker assumiu (lease vencido): não mexe em mais nada."""


class JobShutdown(JobError):
    """Desligamento gracioso: devolve à fila sem gastar a tentativa."""


class Handler(Protocol):
    """Implementação de um tipo de tarefa."""

    spec: JobSpec

    async def run(self, ctx: JobCtx) -> Any: ...

    def final_event(self, job: Job, status: str, detail: str) -> outbox.Event | None: ...


def extra_events(handler: Handler | None, job: Job, status: str, detail: str) -> list[outbox.Event]:
    """Eventos extras do estado final (onda 9: ex. notificação para a Inbox do hub), se o
    handler definir ``final_events(job, status, detail)``; gravados na mesma transação."""
    f = getattr(handler, "final_events", None)
    if f is None:
        return []
    return list(f(job, status, detail))


def _n(status: str) -> int:
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def enqueue_with(
    conn: Any,
    spec: JobSpec,
    payload: Any,
    user: str,
    idempotency_key: str | None = None,
) -> tuple[Job, bool]:
    """Enfileira com ``conn`` (ex.: dentro da transação que também grava a auditoria).
    Com ``idempotency_key``, a mesma chave (e tipo) devolve a existente (``False``)."""
    r = await conn.fetchrow(
        "INSERT INTO hub_jobs (id, kind, payload, idempotency_key, max_attempts, max_duration_s, user_name) "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        "VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "ON CONFLICT (kind, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING "
        f"RETURNING {COLS}",
        new_id("job"),
        spec.kind,
        payload,
        idempotency_key,
        spec.max_attempts,
        int(min(spec.max_duration, 2**31 - 1)),
        user,
    )
    if r is not None:
        return job_of(r), True
    r = await conn.fetchrow(
        f"SELECT {COLS} FROM hub_jobs WHERE kind = $1 AND idempotency_key = $2",  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        spec.kind,
        idempotency_key,
    )
    return job_of(r), False


async def _heartbeat(conn: Any, job_id: str, token: str, lease: float) -> None:
    r = await conn.fetchrow(
        "UPDATE hub_jobs SET lease_until = now() + make_interval(secs => $3), updated_at = now() "
        "WHERE id = $1 AND attempt_token = $2 AND status = 'running' RETURNING cancel_requested",
        job_id,
        token,
        float(lease),
    )
    if r is None:
        raise JobLostLease("lease perdido")
    if r["cancel_requested"]:
        raise JobCancelled("cancelada")


KILLED_BY_MEMORY = (
    "o processo de tarefas foi encerrado pelo sistema (provável falta de memória) enquanto esta "
    "tarefa carregava os dados; ela não será repetida automaticamente — reduza ou divida o arquivo "
    "e envie de novo"
)


async def after_final(handler: Handler | None, job: Job, status: str) -> None:
    """Gancho opcional ``handler.after_final(job, status)`` (síncrono ou ``async``),
    chamado **depois** que o estado terminal foi confirmado (ex.: apagar o upload). Erros
    só vão para o log."""
    fn = getattr(handler, "after_final", None) if handler is not None else None
    if fn is None:
        return
    try:
        r = fn(job, status)
        if inspect.isawaitable(r):
            await r
    except Exception:
        log.exception("tarefa %s: after_final falhou", job.id)


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or ch in "-_")[:100]


class Jobs:
    def __init__(
        self,
        pool: Any,
        owner: str | None = None,
        handlers: Sequence[Handler] = (),
        marker_dir: Path | None = None,
    ) -> None:
        self.pool = pool
        self.owner = owner or f"{os.getpid()}-{random.getrandbits(32):08x}"
        self.handlers = list(handlers)
        # Marcas da "fase de memória" (JobCtx.memory_phase) — ver killed_markers/fail_killed.
        self.marker_dir = marker_dir

    def handler_for(self, kind: str) -> Handler | None:
        return next((h for h in self.handlers if h.spec.kind == kind), None)

    def _final_event(self, j: Job) -> outbox.Event | None:
        h = self.handler_for(j.kind)
        return h.final_event(j, j.status, j.error or "") if h is not None else None

    def take_markers(self, killed_pid: int | None) -> builtins.list[str]:
        """Lê e apaga as marcas de fase de memória deixadas por processos anteriores;
        devolve os ids das tarefas cuja marca é do processo ``killed_pid`` (morto por
        SIGKILL — ex.: OOM do cgroup). Marcas de outros PIDs (container reiniciado,
        caos) só são apagadas: essas tarefas retomam normalmente do checkpoint."""
        d = self.marker_dir
        if d is None or not d.is_dir():
            return []
        out: list[str] = []
        for p in d.glob("*.mem"):
            job_id, _, pid = p.stem.rpartition(".")
            if killed_pid is not None and pid == str(killed_pid) and job_id:
                out.append(job_id)
            with contextlib.suppress(OSError):
                p.unlink()
        return out

    async def enqueue(
        self,
        spec: JobSpec,
        payload: Any,
        user: str,
        idempotency_key: str | None = None,
    ) -> tuple[Job, bool]:
        async with self.pool.acquire() as c:
            return await enqueue_with(c, spec, payload, user, idempotency_key)

    async def get(self, job_id: str) -> Job | None:
        async with self.pool.acquire() as c:
            r = await c.fetchrow(f"SELECT {COLS} FROM hub_jobs WHERE id = $1", job_id)  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        return job_of(r) if r else None

    async def list(self, limit: int) -> builtins.list[Job]:
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                f"SELECT {COLS} FROM hub_jobs ORDER BY created_at DESC LIMIT $1",  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                max(1, min(limit, MAX_LIST)),
            )
        return [job_of(r) for r in rows]

    async def claim(self, specs: Sequence[JobSpec]) -> Job | None:
        kinds = [s.kind for s in specs]
        lease = specs[0].lease if specs else 30.0
        cols = COLS.replace("id, kind", "j.id, j.kind", 1)
        async with self.pool.acquire() as c:
            r = await c.fetchrow(
                "UPDATE hub_jobs j SET status = 'running', attempts = j.attempts + 1, lease_owner = $2, "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                "attempt_token = $4, lease_until = now() + make_interval(secs => $3), started_at = now(), "
                "updated_at = now(), error = CASE WHEN j.status = 'running' "
                "THEN 'retomada após lease vencido' ELSE j.error END "
                "WHERE j.id = (SELECT id FROM hub_jobs WHERE kind = ANY($1::text[]) AND NOT cancel_requested "
                "AND attempts < max_attempts AND ((status = 'queued' AND run_after <= now()) "
                "OR (status = 'running' AND lease_until < now())) "
                "ORDER BY run_after, created_at FOR UPDATE SKIP LOCKED LIMIT 1) "
                f"RETURNING {cols}",
                kinds,
                self.owner,
                float(lease),
                new_id("att"),
            )
            if r is None:
                return None
            job = job_of(r)
            spec = next((s for s in specs if s.kind == job.kind), None)
            if spec is not None:
                await c.execute(
                    "UPDATE hub_jobs SET lease_until = now() + make_interval(secs => $3) "
                    "WHERE id = $1 AND attempt_token = $2",
                    job.id,
                    job.attempt_token,
                    float(spec.lease),
                )
        return job

    async def reap(self, kinds: Sequence[str]) -> builtins.list[Job]:
        """``running`` com lease vencido que não serão retomadas: cancelamento pedido →
        ``cancelled``; sem tentativas → ``dead``. O evento final de cada uma (do handler
        registrado em ``handlers``) é gravado no outbox **na mesma transação** da mudança
        de estado. Devolve as que mudaram (o chamador roda :func:`after_final`)."""
        async with self.pool.acquire() as c, c.transaction():
            a = await c.fetch(
                "UPDATE hub_jobs SET status = 'cancelled', finished_at = now(), updated_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                "lease_owner = NULL, lease_until = NULL, attempt_token = NULL, "
                "error = 'cancelada a pedido (o processo caiu antes de parar)' "
                "WHERE kind = ANY($1::text[]) AND status = 'running' AND lease_until < now() "
                f"AND cancel_requested RETURNING {COLS}",
                list(kinds),
            )
            b = await c.fetch(
                "UPDATE hub_jobs SET status = 'dead', finished_at = now(), updated_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                "lease_owner = NULL, lease_until = NULL, attempt_token = NULL, "
                "error = coalesce(error || ' · ', '') || 'o processo caiu durante a última tentativa permitida' "
                "WHERE kind = ANY($1::text[]) AND status = 'running' AND lease_until < now() "
                f"AND attempts >= max_attempts RETURNING {COLS}",
                list(kinds),
            )
            out = [job_of(r) for r in [*a, *b]]
            for j in out:
                ev = self._final_event(j)
                if ev is not None:
                    await outbox.write(c, ev)
                for x in extra_events(self.handler_for(j.kind), j, j.status, j.error or ""):
                    await outbox.write(c, x)
        return out

    async def fail_killed(self, job_ids: Sequence[str], owner_prefix: str, reason: str) -> builtins.list[Job]:
        """``failed`` (sem novas tentativas) para as tarefas ``running`` de ``job_ids`` cujo
        lease ainda é do processo morto (``lease_owner`` começa com ``owner_prefix``, ex.
        ``"1234-"``), com o evento final na mesma transação. Para quando o processo de
        tarefas foi morto (SIGKILL/OOM) no meio de uma fase de memória: repetir a tentativa
        só mataria o processo de novo."""
        if not job_ids:
            return []
        async with self.pool.acquire() as c, c.transaction():
            rows = await c.fetch(
                "UPDATE hub_jobs SET status = 'failed', error = $3, finished_at = now(), updated_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                "lease_owner = NULL, lease_until = NULL, attempt_token = NULL "
                "WHERE id = ANY($1::text[]) AND status = 'running' AND starts_with(lease_owner, $2) "
                f"RETURNING {COLS}",
                list(job_ids),
                owner_prefix,
                reason,
            )
            out = [job_of(r) for r in rows]
            for j in out:
                ev = self._final_event(j)
                if ev is not None:
                    await outbox.write(c, ev)
                for x in extra_events(self.handler_for(j.kind), j, j.status, j.error or ""):
                    await outbox.write(c, x)
        for j in out:
            await after_final(self.handler_for(j.kind), j, j.status)
        return out

    async def cancel(self, job_id: str) -> Job:
        now_cancel = (
            "(status = 'queued' OR (status = 'running' AND (lease_until IS NULL OR lease_until < now())))"
        )
        async with self.pool.acquire() as c:
            r = await c.fetchrow(
                "UPDATE hub_jobs SET cancel_requested = true, updated_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                f"error = CASE WHEN {now_cancel} THEN 'cancelada a pedido' ELSE error END, "
                f"lease_owner = CASE WHEN {now_cancel} THEN NULL ELSE lease_owner END, "
                f"attempt_token = CASE WHEN {now_cancel} THEN NULL ELSE attempt_token END, "
                f"lease_until = CASE WHEN {now_cancel} THEN NULL ELSE lease_until END, "
                f"finished_at = CASE WHEN {now_cancel} THEN now() ELSE finished_at END, "
                f"status = CASE WHEN {now_cancel} THEN 'cancelled' ELSE status END "
                f"WHERE id = $1 AND status IN ('queued', 'running') RETURNING {COLS}",
                job_id,
            )
        if r is not None:
            j = job_of(r)
            if j.status == "cancelled":
                await after_final(self.handler_for(j.kind), j, j.status)
            return j
        cur = await self.get(job_id)
        if cur is None:
            raise LookupError("Tarefa não encontrada.")
        raise ValueError(f'A tarefa não pode ser cancelada no estado "{label(cur.status)}".')

    async def retry(self, job_id: str) -> Job:
        async with self.pool.acquire() as c:
            r = await c.fetchrow(
                "UPDATE hub_jobs SET status = 'queued', attempts = 0, run_after = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                "cancel_requested = false, error = NULL, finished_at = NULL, lease_owner = NULL, "
                "lease_until = NULL, attempt_token = NULL, updated_at = now() "
                f"WHERE id = $1 AND status IN ('failed', 'dead', 'cancelled') RETURNING {COLS}",
                job_id,
            )
        if r is not None:
            return job_of(r)
        j = await self.get(job_id)
        if j is None:
            raise LookupError("Tarefa não encontrada.")
        raise ValueError(
            "Só tarefas com falha, esgotadas ou canceladas podem ser reprocessadas "
            f"(estado atual: {label(j.status)})."
        )

    async def heartbeat(self, job_id: str, token: str, lease: float) -> None:
        async with self.pool.acquire() as c:
            await _heartbeat(c, job_id, token, lease)

    async def finish(
        self, spec: JobSpec, handler: Handler, job: Job, outcome: Any, error: JobError | None
    ) -> None:
        """Grava o resultado da tentativa (estado + evento final na mesma transação); num
        estado terminal, chama :func:`after_final` depois de confirmar."""
        fin = await self._finish_tx(spec, handler, job, outcome, error)
        if fin is not None:
            await after_final(handler, fin, fin.status)

    async def _finish_tx(
        self, spec: JobSpec, handler: Handler, job: Job, outcome: Any, error: JobError | None
    ) -> Job | None:
        if isinstance(error, JobLostLease):
            return None
        owned = "WHERE id = $1 AND attempt_token = $2 AND status = 'running'"
        token = job.attempt_token or ""
        async with self.pool.acquire() as c, c.transaction():
            if isinstance(error, JobShutdown):
                await c.execute(
                    "UPDATE hub_jobs SET status = 'queued', attempts = greatest(attempts - 1, 0), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                    "lease_owner = NULL, lease_until = NULL, attempt_token = NULL, run_after = now(), "
                    f"updated_at = now() {owned}",
                    job.id,
                    token,
                )
                return None
            fin = replace(job)
            detail = ""
            if error is None:
                fin.result = outcome
                n = _n(
                    await c.execute(
                        "UPDATE hub_jobs SET status = 'done', result = $3, error = NULL, finished_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                        "updated_at = now(), lease_owner = NULL, lease_until = NULL, attempt_token = NULL, "
                        f"progress_done = greatest(progress_done, progress_total) {owned}",
                        job.id,
                        token,
                        outcome,
                    )
                )
                status = "done"
            elif isinstance(error, JobCancelled):
                detail = "cancelada a pedido"
                n = _n(
                    await c.execute(
                        "UPDATE hub_jobs SET status = 'cancelled', error = 'cancelada a pedido', "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                        "finished_at = now(), updated_at = now(), lease_owner = NULL, lease_until = NULL, "
                        f"attempt_token = NULL {owned}",
                        job.id,
                        token,
                    )
                )
                status = "cancelled"
            elif isinstance(error, JobFail):
                detail = str(error)
                n = _n(
                    await c.execute(
                        "UPDATE hub_jobs SET status = 'failed', error = $3, finished_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                        "updated_at = now(), lease_owner = NULL, lease_until = NULL, attempt_token = NULL "
                        f"{owned}",
                        job.id,
                        token,
                        detail,
                    )
                )
                status = "failed"
            else:  # JobRetry (ou erro inesperado)
                detail = str(error) or error.__class__.__name__
                if job.attempts >= spec.max_attempts:
                    n = _n(
                        await c.execute(
                            "UPDATE hub_jobs SET status = 'dead', error = $3, finished_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                            "updated_at = now(), lease_owner = NULL, lease_until = NULL, attempt_token = NULL "
                            f"{owned}",
                            job.id,
                            token,
                            detail,
                        )
                    )
                    status = "dead"
                else:
                    wait = backoff(job.attempts, spec.backoff_base, spec.backoff_max, random.random())  # noqa: S311
                    await c.execute(
                        "UPDATE hub_jobs SET status = 'queued', error = $3, "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
                        "run_after = now() + make_interval(secs => $4), updated_at = now(), "
                        f"lease_owner = NULL, lease_until = NULL, attempt_token = NULL {owned}",
                        job.id,
                        token,
                        detail,
                        float(wait),
                    )
                    return None
            if n == 0:
                return None  # não é mais nossa
            fin.status = status
            if detail:
                fin.error = detail
            ev = handler.final_event(fin, status, detail)
            if ev is not None:
                await outbox.write(c, ev)
            for x in extra_events(handler, fin, status, detail):
                await outbox.write(c, x)
        return fin


class JobCtx:
    """Contexto de uma tentativa em execução."""

    def __init__(self, job: Job, jobs: Jobs, spec: JobSpec, shutdown: Shutdown) -> None:
        self.job = job
        self.jobs = jobs
        self.spec = spec
        self.shutdown = shutdown
        self.cancelled = False
        self.lost = False

    @property
    def pool(self) -> Any:
        return self.jobs.pool

    @property
    def checkpoint(self) -> Any:
        """Checkpoint gravado na última confirmação (retomada)."""
        return self.job.checkpoint

    @contextlib.contextmanager
    def memory_phase(self) -> Generator[None]:
        """Marca (arquivo em ``Jobs.marker_dir``) enquanto a tentativa segura muita memória
        (ex.: a aba inteira carregada pelo python-calamine). Se o processo de tarefas for
        morto por SIGKILL (OOM) com a marca presente, o próximo processo marca a tarefa
        ``failed`` em vez de repeti-la (:meth:`Jobs.fail_killed`)."""
        d = self.jobs.marker_dir
        p: Path | None = None
        if d is not None:
            try:
                d.mkdir(parents=True, exist_ok=True)
                p = d / f"{_safe(self.job.id)}.{os.getpid()}.mem"
                p.write_text(self.jobs.owner, "utf-8")
            except OSError:
                p = None
        try:
            yield
        finally:
            if p is not None:
                with contextlib.suppress(OSError):
                    p.unlink()

    def should_stop(self) -> JobError | None:
        """Motivo para parar agora (lease perdido, cancelamento, desligamento)."""
        if self.lost:
            return JobLostLease("lease perdido")
        if self.cancelled:
            return JobCancelled("cancelada")
        if self.shutdown.triggered:
            return JobShutdown("desligamento")
        return None

    def raise_if_stopping(self) -> None:
        e = self.should_stop()
        if e is not None:
            raise e

    async def save_checkpoint(
        self, conn: Any, checkpoint: Any, done: int, total: int, text: str = ""
    ) -> None:
        """Grava checkpoint + progresso (e renova o lease) com ``conn`` — a transação do
        lote. Levanta :class:`JobLostLease`/:class:`JobCancelled` (a transação deve ser
        descartada — sair do ``async with conn.transaction()`` com a exceção faz isso)."""
        r = await conn.fetchrow(
            "UPDATE hub_jobs SET checkpoint = $3, progress_done = $4, progress_total = $5, progress_text = $6, "
            "lease_until = now() + make_interval(secs => $7), updated_at = now() "
            "WHERE id = $1 AND attempt_token = $2 AND status = 'running' RETURNING cancel_requested",
            self.job.id,
            self.job.attempt_token or "",
            checkpoint,
            int(done),
            int(total),
            text,
            float(self.spec.lease),
        )
        if r is None:
            self.lost = True
            raise JobLostLease("lease perdido")
        if r["cancel_requested"]:
            self.cancelled = True
            raise JobCancelled("cancelada")

    async def progress(self, done: int, total: int, text: str = "") -> None:
        """Progresso sem checkpoint (fora de transação)."""
        async with self.jobs.pool.acquire() as c:
            n = _n(
                await c.execute(
                    "UPDATE hub_jobs SET progress_done = $3, progress_total = $4, progress_text = $5, "
                    "updated_at = now() WHERE id = $1 AND attempt_token = $2 AND status = 'running'",
                    self.job.id,
                    self.job.attempt_token or "",
                    int(done),
                    int(total),
                    text,
                )
            )
        if n == 0:
            self.lost = True
            raise JobLostLease("lease perdido")


async def run_claimed(jobs: Jobs, handler: Handler, job: Job, shutdown: Shutdown) -> None:
    """Executa uma tentativa já assumida: heartbeat em paralelo, tempo máximo, resultado."""
    spec = handler.spec
    ctx = JobCtx(job, jobs, spec, shutdown)
    token = job.attempt_token or ""

    async def heartbeat() -> None:
        every = max(spec.lease / 3, 0.3)
        last_ok = time.monotonic()
        while True:
            await asyncio.sleep(every)
            try:
                await jobs.heartbeat(job.id, token, spec.lease)
                last_ok = time.monotonic()
            except JobLostLease:
                ctx.lost = True
                return
            except JobCancelled:
                last_ok = time.monotonic()
                ctx.cancelled = True
            except Exception:
                if time.monotonic() - last_ok >= spec.lease:
                    ctx.lost = True
                    return

    hb = asyncio.create_task(heartbeat())
    work = asyncio.create_task(asyncio.wait_for(handler.run(ctx), spec.max_duration))
    outcome: Any = None
    err: JobError | None = None
    try:
        done, _ = await asyncio.wait({hb, work}, return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            try:
                outcome = work.result()
            except TimeoutError:
                err = JobRetry(f"tempo máximo da tentativa excedido ({int(spec.max_duration)} s)")
            except JobError as e:
                err = e
            except Exception as e:
                log.exception("tarefa %s: erro", job.id)
                err = JobRetry(f"{e.__class__.__name__}: {e}")
        else:
            # Lease perdido: a tentativa é interrompida (transações abertas desfeitas).
            work.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await work
            err = JobLostLease("lease perdido")
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
    if isinstance(err, JobRetry) and ctx.cancelled:
        err = JobCancelled("cancelada")
    elif err is not None and ctx.lost:
        err = JobLostLease("lease perdido")
    try:
        await jobs.finish(spec, handler, job, outcome, err)
    except Exception:
        log.exception("tarefa %s: falha ao gravar o resultado", job.id)


class Worker:
    """Assume tarefas dos tipos dos ``handlers`` (uma vaga de :class:`Heavy` por
    tarefa), executa em paralelo, retoma leases vencidos e marca ``dead`` o que esgotou."""

    def __init__(
        self,
        jobs: Jobs,
        handlers: Sequence[Handler],
        heavy: Heavy,
        shutdown: Shutdown,
        poll: float = 2.0,
        ready: Callable[[], bool] | None = None,
        on_ready: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.jobs = jobs
        if not jobs.handlers:
            jobs.handlers = list(handlers)
        # Executado uma vez, na primeira volta com o banco pronto, antes de assumir tarefas.
        self.on_ready = on_ready
        self.handlers = list(handlers)
        self.heavy = heavy
        self.shutdown = shutdown
        self.poll = poll
        self.ready = ready or (lambda: True)
        self._wake = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()

    def wake(self) -> None:
        self._wake.set()

    def spawn(self) -> asyncio.Task[None]:
        return asyncio.create_task(self.run())

    async def run(self) -> None:
        specs = [h.spec for h in self.handlers]
        kinds = [s.kind for s in specs]
        while not self.shutdown.triggered:
            if self.ready():
                if self.on_ready is not None:
                    fn, self.on_ready = self.on_ready, None
                    try:
                        await fn()
                    except Exception as e:
                        log.warning("worker: preparação: %s", e)
                try:
                    reaped = await self.jobs.reap(kinds)
                except Exception as e:
                    log.warning("worker: reap: %s", e)
                    reaped = []
                for j in reaped:
                    await after_final(self.jobs.handler_for(j.kind), j, j.status)
                claimed = False
                permit = self.heavy.try_acquire()
                if permit is not None:
                    try:
                        job = await self.jobs.claim(specs)
                    except Exception as e:
                        log.warning("worker de tarefas: %s", e)
                        job = None
                    h = next((h for h in self.handlers if job and h.spec.kind == job.kind), None)
                    if job is not None and h is not None:
                        claimed = True
                        inflight = self.shutdown.track()

                        async def go(
                            h: Handler = h, job: Job = job, permit: Any = permit, inf: Any = inflight
                        ) -> None:
                            try:
                                await run_claimed(self.jobs, h, job, self.shutdown)
                            finally:
                                permit.release()
                                inf.done()

                        t = asyncio.create_task(go())
                        self._tasks.add(t)
                        t.add_done_callback(self._tasks.discard)
                    else:
                        permit.release()
                if claimed:
                    continue
            await self._sleep()

    async def _sleep(self) -> None:
        self._wake.clear()
        waits: list[asyncio.Task[Any]] = [
            asyncio.create_task(self._wake.wait()),
            asyncio.create_task(self.shutdown.wait()),
        ]
        try:
            await asyncio.wait(waits, timeout=self.poll, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waits:
                w.cancel()


AsyncFn = Callable[[], Awaitable[None]]
