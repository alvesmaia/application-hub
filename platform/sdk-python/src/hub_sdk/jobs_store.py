"""Queue operations of the durable jobs (:mod:`hub_sdk.jobs`) over ``hub_jobs``: enqueue, claim,
cancel, retry, reap and the attempt's final state (with its outbox events in the same transaction)."""

from __future__ import annotations

import builtins
import contextlib
import os
import random
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from hub_sdk import _jobs_sql as sql
from hub_sdk import outbox
from hub_sdk.jobs_model import (
    MAX_LIST,
    Handler,
    Job,
    JobCancelled,
    JobError,
    JobFail,
    JobLostLease,
    JobShutdown,
    JobSpec,
    after_final,
    backoff,
    extra_events,
    job_of,
    label,
)


async def enqueue_with(
    conn: Any,
    spec: JobSpec,
    payload: Any,
    user: str,
    idempotency_key: str | None = None,
) -> tuple[Job, bool]:
    """Enqueues with ``conn`` (e.g. inside the transaction that also writes the audit).
    With ``idempotency_key``, the same key (and type) returns the existing one (``False``)."""
    secs = int(min(spec.max_duration, 2**31 - 1))
    r = await sql.insert_job(conn, spec.kind, payload, idempotency_key, spec.max_attempts, secs, user)
    if r is not None:
        return job_of(r), True
    return job_of(await sql.select_by_key(conn, spec.kind, idempotency_key)), False


async def _write_final_events(conn: Any, handler: Handler | None, job: Job, status: str, detail: str) -> None:
    """Final event + extra events, with ``conn`` (the transaction of the state change)."""
    ev = handler.final_event(job, status, detail) if handler is not None else None
    if ev is not None:
        await outbox.write(conn, ev)
    for x in extra_events(handler, job, status, detail):
        await outbox.write(conn, x)


def _final_state(spec: JobSpec, job: Job, error: JobError | None) -> tuple[str, str]:
    """``(status, detail)`` an attempt ends in; ``shutdown``/``retry`` mean back to the queue."""
    if error is None:
        return "done", ""
    if isinstance(error, JobShutdown):
        return "shutdown", ""
    if isinstance(error, JobCancelled):
        return "cancelled", "cancelada a pedido"
    if isinstance(error, JobFail):
        return "failed", str(error)
    detail = str(error) or error.__class__.__name__  # JobRetry (or unexpected error)
    return ("dead" if job.attempts >= spec.max_attempts else "retry"), detail


async def _write_final_state(conn: Any, job: Job, token: str, status: str, outcome: Any, detail: str) -> int:
    if status == "done":
        return await sql.finish_done(conn, job.id, token, outcome)
    if status == "cancelled":
        return await sql.finish_cancelled(conn, job.id, token)
    return await sql.finish_with_error(conn, status, job.id, token, detail)


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
        # Markers of the "memory phase" (JobCtx.memory_phase) — see killed_markers/fail_killed.
        self.marker_dir = marker_dir

    def handler_for(self, kind: str) -> Handler | None:
        return next((h for h in self.handlers if h.spec.kind == kind), None)

    def take_markers(self, killed_pid: int | None) -> builtins.list[str]:
        """Reads and deletes the memory-phase markers left by previous processes;
        returns the ids of the jobs whose marker belongs to the ``killed_pid`` process (killed by
        SIGKILL — e.g. cgroup OOM). Markers of other PIDs (container restarted,
        chaos) are only deleted: those jobs resume normally from the checkpoint."""
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
            r = await sql.select_one(c, job_id)
        return job_of(r) if r else None

    async def list(self, limit: int) -> builtins.list[Job]:
        async with self.pool.acquire() as c:
            rows = await sql.select_recent(c, max(1, min(limit, MAX_LIST)))
        return [job_of(r) for r in rows]

    async def claim(self, specs: Sequence[JobSpec]) -> Job | None:
        lease = specs[0].lease if specs else 30.0
        async with self.pool.acquire() as c:
            r = await sql.claim_row(c, [s.kind for s in specs], self.owner, lease)
            if r is None:
                return None
            job = job_of(r)
            spec = next((s for s in specs if s.kind == job.kind), None)
            if spec is not None:
                await sql.set_lease(c, job.id, job.attempt_token, spec.lease)
        return job

    async def reap(self, kinds: Sequence[str]) -> builtins.list[Job]:
        """``running`` with an expired lease that will not be resumed: cancellation requested →
        ``cancelled``; no attempts left → ``dead``. The final event of each one (from the handler
        registered in ``handlers``) is written to the outbox **in the same transaction** as the state change.
        Returns the ones that changed (the caller runs :func:`after_final`)."""
        async with self.pool.acquire() as c, c.transaction():
            out = [job_of(r) for r in await sql.reap_rows(c, list(kinds))]
            for j in out:
                await _write_final_events(c, self.handler_for(j.kind), j, j.status, j.error or "")
        return out

    async def fail_killed(self, job_ids: Sequence[str], owner_prefix: str, reason: str) -> builtins.list[Job]:
        """``failed`` (no further attempts) for the ``running`` jobs in ``job_ids`` whose
        lease still belongs to the dead process (``lease_owner`` starts with ``owner_prefix``, e.g.
        ``"1234-"``), with the final event in the same transaction. Used when the jobs process
        was killed (SIGKILL/OOM) in the middle of a memory phase: retrying the attempt
        would only kill the process again."""
        if not job_ids:
            return []
        async with self.pool.acquire() as c, c.transaction():
            out = [job_of(r) for r in await sql.fail_killed_rows(c, list(job_ids), owner_prefix, reason)]
            for j in out:
                await _write_final_events(c, self.handler_for(j.kind), j, j.status, j.error or "")
        for j in out:
            await after_final(self.handler_for(j.kind), j, j.status)
        return out

    async def cancel(self, job_id: str) -> Job:
        async with self.pool.acquire() as c:
            r = await sql.cancel_row(c, job_id)
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
            r = await sql.retry_row(c, job_id)
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
            r = await sql.heartbeat_row(c, job_id, token, lease)
            if r is None:
                raise JobLostLease("lease perdido")
            if r["cancel_requested"]:
                raise JobCancelled("cancelada")

    async def finish(
        self, spec: JobSpec, handler: Handler, job: Job, outcome: Any, error: JobError | None
    ) -> None:
        """Records the attempt's result (state + final event in the same transaction); in a
        terminal state, calls :func:`after_final` after committing."""
        fin = await self._finish_tx(spec, handler, job, outcome, error)
        if fin is not None:
            await after_final(handler, fin, fin.status)

    async def _finish_tx(
        self, spec: JobSpec, handler: Handler, job: Job, outcome: Any, error: JobError | None
    ) -> Job | None:
        if isinstance(error, JobLostLease):
            return None
        token = job.attempt_token or ""
        async with self.pool.acquire() as c, c.transaction():
            status, detail = _final_state(spec, job, error)
            if status == "shutdown":
                await sql.requeue_shutdown(c, job.id, token)
                return None
            if status == "retry":
                wait = backoff(job.attempts, spec.backoff_base, spec.backoff_max, random.random())  # noqa: S311
                await sql.requeue_retry(c, job.id, token, detail, wait)
                return None
            if await _write_final_state(c, job, token, status, outcome, detail) == 0:
                return None  # no longer owned by us
            fin = replace(job, status=status)
            if error is None:
                fin.result = outcome
            if detail:
                fin.error = detail
            await _write_final_events(c, handler, fin, status, detail)
        return fin
