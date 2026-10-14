"""Durable jobs in the ``hub_jobs`` table (table, SQL and states have a stable format: do not change without a migration).

Life cycle: ``queued`` → ``running`` → ``done`` | ``failed`` (definitive error) |
``dead`` (tentativas esgotadas: dead-letter) | ``cancelled``. Falha transitória volta a
``queued`` with ``run_after`` = now + :func:`backoff` (exponential with *equal jitter*).
:meth:`Jobs.retry` returns a terminal job to the queue, keeping the checkpoint.

- **Assumir** (:meth:`Jobs.claim`): ``UPDATE … WHERE id = (SELECT … FOR UPDATE SKIP
  LOCKED LIMIT 1)`` over ``queued`` jobs past their time, or ``running`` with an **expired lease**.
- **Per-attempt fence**: each claim writes a new ``attempt_token``; heartbeat,
  checkpoint, progress and completion only count with the current attempt's token.
- **Lease**: renewed every ``lease/3`` and at each checkpoint; if it cannot be renewed for a whole lease
  period (DB down), the worker stops.
- **Checkpoint** (:meth:`JobCtx.save_checkpoint`): written with the batch's connection/transaction —
  the batch and the checkpoint commit together or not at all.
- **Maximum time** per attempt; **cancel**; **final event** in the outbox in the same
  transaction as the state change.

CPU work of a Python handler must go to
``asyncio.to_thread`` (the event loop must stay free for the heartbeat and the
HTTP); the :class:`~hub_sdk.heavy.Heavy` semaphore is per process.
"""

# The implementation lives in jobs_model / jobs_store / jobs_worker; this module keeps the public names.
from hub_sdk._jobs_sql import COLS
from hub_sdk.jobs_model import (
    KILLED_BY_MEMORY,
    LABELS,
    MAX_LIST,
    STATUSES,
    TERMINAL,
    Handler,
    Job,
    JobCancelled,
    JobError,
    JobFail,
    JobLostLease,
    JobRetry,
    JobShutdown,
    JobSpec,
    after_final,
    backoff,
    extra_events,
    job_of,
    label,
    log,
)
from hub_sdk.jobs_store import Jobs, enqueue_with
from hub_sdk.jobs_worker import AsyncFn, JobCtx, Worker, run_claimed

__all__ = [
    "COLS",
    "KILLED_BY_MEMORY",
    "LABELS",
    "MAX_LIST",
    "STATUSES",
    "TERMINAL",
    "AsyncFn",
    "Handler",
    "Job",
    "JobCancelled",
    "JobCtx",
    "JobError",
    "JobFail",
    "JobLostLease",
    "JobRetry",
    "JobShutdown",
    "JobSpec",
    "Jobs",
    "Worker",
    "after_final",
    "backoff",
    "enqueue_with",
    "extra_events",
    "job_of",
    "label",
    "log",
    "run_claimed",
]
