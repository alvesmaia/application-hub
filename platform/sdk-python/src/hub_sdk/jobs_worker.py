"""Running the durable jobs (:mod:`hub_sdk.jobs`): the attempt's context (checkpoint, progress,
memory phase), the heartbeat that fences it and the worker that claims jobs."""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import os
import time
from collections.abc import Awaitable, Callable, Generator, Sequence
from pathlib import Path
from typing import Any

from hub_sdk import _jobs_sql as sql
from hub_sdk.heavy import Heavy
from hub_sdk.jobs_model import (
    Handler,
    Job,
    JobCancelled,
    JobError,
    JobLostLease,
    JobRetry,
    JobShutdown,
    JobSpec,
    after_final,
    log,
)
from hub_sdk.jobs_store import Jobs
from hub_sdk.shutdown import Shutdown


def _safe(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or ch in "-_")[:100]


class JobCtx:
    """Context of an attempt in progress."""

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
        """Checkpoint written at the last commit (resume)."""
        return self.job.checkpoint

    @contextlib.contextmanager
    def memory_phase(self) -> Generator[None]:
        """Marker (file in ``Jobs.marker_dir``) while the attempt holds a lot of memory
        (e.g. the whole sheet loaded by python-calamine). If the jobs process is
        killed by SIGKILL (OOM) with the marker present, the next process marks the job
        ``failed`` instead of retrying it (:meth:`Jobs.fail_killed`)."""
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
        """Reason to stop now (lost lease, cancellation, shutdown)."""
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
        """Writes checkpoint + progress (and renews the lease) with ``conn`` — the batch's
        transaction. Raises :class:`JobLostLease`/:class:`JobCancelled` (the transaction must be
        discarded — exiting the ``async with conn.transaction()`` with the exception does that)."""
        r = await sql.save_checkpoint_row(
            conn,
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
        """Progress without a checkpoint (outside a transaction)."""
        async with self.jobs.pool.acquire() as c:
            token = self.job.attempt_token or ""
            n = await sql.progress_count(c, self.job.id, token, int(done), int(total), text)
        if n == 0:
            self.lost = True
            raise JobLostLease("lease perdido")


async def _heartbeat_loop(ctx: JobCtx) -> None:
    """Renews the lease every ``lease/3``; gives up (``ctx.lost``) when another worker took over
    or when the DB stayed unreachable for a whole lease period."""
    spec, job = ctx.spec, ctx.job
    every = max(spec.lease / 3, 0.3)
    last_ok = time.monotonic()
    while True:
        await asyncio.sleep(every)
        try:
            await ctx.jobs.heartbeat(job.id, job.attempt_token or "", spec.lease)
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


def _outcome_of(work: asyncio.Task[Any], ctx: JobCtx) -> tuple[Any, JobError | None]:
    try:
        return work.result(), None
    except TimeoutError:
        return None, JobRetry(f"tempo máximo da tentativa excedido ({int(ctx.spec.max_duration)} s)")
    except JobError as e:
        return None, e
    except Exception as e:
        log.exception("tarefa %s: erro", ctx.job.id)
        return None, JobRetry(f"{e.__class__.__name__}: {e}")


async def _attempt(ctx: JobCtx, handler: Handler) -> tuple[Any, JobError | None]:
    """The handler's run raced against the heartbeat: whichever ends first decides."""
    hb = asyncio.create_task(_heartbeat_loop(ctx))
    work = asyncio.create_task(asyncio.wait_for(handler.run(ctx), ctx.spec.max_duration))
    try:
        done, _ = await asyncio.wait({hb, work}, return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            return _outcome_of(work, ctx)
        # Lease lost: the attempt is interrupted (open transactions rolled back).
        work.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await work
        return None, JobLostLease("lease perdido")
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb


async def run_claimed(jobs: Jobs, handler: Handler, job: Job, shutdown: Shutdown) -> None:
    """Runs an already claimed attempt: parallel heartbeat, maximum time, result."""
    spec = handler.spec
    ctx = JobCtx(job, jobs, spec, shutdown)
    outcome, err = await _attempt(ctx, handler)
    if isinstance(err, JobRetry) and ctx.cancelled:
        err = JobCancelled("cancelada")
    elif err is not None and ctx.lost:
        err = JobLostLease("lease perdido")
    try:
        await jobs.finish(spec, handler, job, outcome, err)
    except Exception:
        log.exception("tarefa %s: falha ao gravar o resultado", job.id)


class Worker:
    """Claims jobs of the types in ``handlers`` (one :class:`Heavy` slot per
    job), runs them in parallel, resumes expired leases and marks ``dead`` what ran out of attempts."""

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
        # Runs once, on the first pass with the DB ready, before claiming jobs.
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
            # A claimed job means there may be more: look again right away instead of sleeping.
            if self.ready() and await self._pass(specs, kinds):
                continue
            await self._sleep()

    async def _pass(self, specs: builtins.list[JobSpec], kinds: builtins.list[str]) -> bool:
        await self._prepare_once()
        await self._reap(kinds)
        return await self._claim_one(specs)

    async def _prepare_once(self) -> None:
        if self.on_ready is None:
            return
        fn, self.on_ready = self.on_ready, None
        try:
            await fn()
        except Exception as e:
            log.warning("worker: preparação: %s", e)

    async def _reap(self, kinds: builtins.list[str]) -> None:
        try:
            reaped = await self.jobs.reap(kinds)
        except Exception as e:
            log.warning("worker: reap: %s", e)
            return
        for j in reaped:
            await after_final(self.jobs.handler_for(j.kind), j, j.status)

    async def _claim_one(self, specs: builtins.list[JobSpec]) -> bool:
        permit = self.heavy.try_acquire()
        if permit is None:
            return False
        try:
            job = await self.jobs.claim(specs)
        except Exception as e:
            log.warning("worker de tarefas: %s", e)
            job = None
        h = next((h for h in self.handlers if job and h.spec.kind == job.kind), None)
        if job is None or h is None:
            permit.release()
            return False
        self._start(h, job, permit)
        return True

    def _start(self, h: Handler, job: Job, permit: Any) -> None:
        inflight = self.shutdown.track()

        async def go() -> None:
            try:
                await run_claimed(self.jobs, h, job, self.shutdown)
            finally:
                permit.release()
                inflight.done()

        t = asyncio.create_task(go())
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

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
