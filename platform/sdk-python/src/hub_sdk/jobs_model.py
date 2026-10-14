"""Model of the durable jobs (:mod:`hub_sdk.jobs`): states, contract of a job type, the job row,
the attempt outcomes and the handler protocol."""

from __future__ import annotations

import inspect
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from hub_sdk import outbox

if TYPE_CHECKING:
    from hub_sdk.jobs_worker import JobCtx

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
    """Contract of a job type (mirrors ``manifest.jobs``)."""

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
    """Wait before attempt ``attempt + 1``: ``base · 2^(attempt-1)`` capped at
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
        """JSON of ``GET /_hub/jobs`` (without the ``attempt_token``, which never leaves the app)."""
        d = {k: getattr(self, k) for k in self.__dataclass_fields__ if k != "attempt_token"}
        return d

    @property
    def percent(self) -> int:
        if self.status == "done":
            return 100
        if self.progress_total <= 0:
            return 0
        return max(0, min(100, self.progress_done * 100 // self.progress_total))


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
    """Result of an attempt that did not complete."""


class JobRetry(JobError):
    """Transient failure: retry with backoff (or ``dead`` if attempts are exhausted)."""


class JobFail(JobError):
    """Definitive failure (invalid input, etc.): ``failed``, no further attempts."""


class JobCancelled(JobError):
    """Cancelamento pedido."""


class JobLostLease(JobError):
    """Another worker took over (expired lease): does not touch anything else."""


class JobShutdown(JobError):
    """Graceful shutdown: returns to the queue without spending the attempt."""


class Handler(Protocol):
    """Implementation of a job type."""

    spec: JobSpec

    async def run(self, ctx: JobCtx) -> Any: ...

    def final_event(self, job: Job, status: str, detail: str) -> outbox.Event | None: ...


def extra_events(handler: Handler | None, job: Job, status: str, detail: str) -> list[outbox.Event]:
    """Extra events of the final state (wave 9: e.g. notification to the hub Inbox), if the
    handler defines ``final_events(job, status, detail)``; written in the same transaction."""
    f = getattr(handler, "final_events", None)
    if f is None:
        return []
    return list(f(job, status, detail))


KILLED_BY_MEMORY = (
    "o processo de tarefas foi encerrado pelo sistema (provável falta de memória) enquanto esta "
    "tarefa carregava os dados; ela não será repetida automaticamente — reduza ou divida o arquivo "
    "e envie de novo"
)


async def after_final(handler: Handler | None, job: Job, status: str) -> None:
    """Optional hook ``handler.after_final(job, status)`` (sync or ``async``),
    called **after** the terminal state is committed (e.g. deleting the upload). Errors
    only go to the log."""
    fn = getattr(handler, "after_final", None) if handler is not None else None
    if fn is None:
        return
    try:
        r = fn(job, status)
        if inspect.isawaitable(r):
            await r
    except Exception:
        log.exception("tarefa %s: after_final falhou", job.id)
