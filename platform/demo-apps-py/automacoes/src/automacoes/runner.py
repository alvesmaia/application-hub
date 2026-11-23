"""Durable job ``automacoes.run``: runs the package version pinned in the run, in a subprocess
(:mod:`automacoes.executor`). The run moves to ``processando`` when an attempt starts; its terminal
status follows the job's (``after_final``): done → ``concluido``, failed/dead → ``erro``,
cancelled → ``cancelado``. A package error or timeout fails at once (``JobFail``: package code is
not assumed idempotent); a user cancel kills the subprocess within ``POLL_S``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hub_sdk import outbox
from hub_sdk.app import HubSdk
from hub_sdk.jobs import Job, JobCancelled, JobCtx, JobFail

from automacoes import executor, packages, store
from automacoes.model import CANCELLED, DONE, ERROR, JOB_SPEC, TERMINAL, Run

JOB_TO_RUN = {"done": DONE, "failed": ERROR, "dead": ERROR, "cancelled": CANCELLED}
FINAL_ACTION = {DONE: "Execução concluída", ERROR: "Execução com erro"}


def run_id_of(job: Job) -> int:
    payload = job.payload if isinstance(job.payload, dict) else {}
    try:
        return int(payload.get("run_id", 0))
    except (TypeError, ValueError):
        return 0


class RunHandler:
    spec = JOB_SPEC

    def __init__(self, scratch: Path) -> None:
        self.scratch = scratch
        self.sdk: HubSdk | None = None  # set by create_app (the SDK needs the handler first)

    async def run(self, ctx: JobCtx) -> Any:
        run = await store.start_run(ctx.pool, run_id_of(ctx.job))
        if run is None:
            return {"ignorada": "execução já encerrada"}
        content = await packages.content(ctx.pool, run.package_name, run.package_version)
        if content is None:
            raise JobFail(f"pacote {run.package_name} {run.package_version} não encontrado")
        out = await executor.execute(
            content,
            await self._entrypoint(ctx, run),
            run.params,
            run.timeout_s,
            self.scratch,
            self._stop(ctx, run),
        )
        await store.save_log(ctx.pool, run.id, out.log)
        if out.error is not None:
            raise JobFail(out.error)
        return out.output

    async def _entrypoint(self, ctx: JobCtx, run: Run) -> str:
        pkg = await packages.get_package(ctx.pool, run.package_name, run.package_version)
        if pkg is None:
            raise JobFail(f"pacote {run.package_name} {run.package_version} não encontrado")
        return pkg.entrypoint

    def _stop(self, ctx: JobCtx, run: Run) -> executor.StopCheck:
        """Why the subprocess must die now: the SDK's reasons (cancel, shutdown, lost lease) or the
        run already terminal (cancelled on the screen/API)."""

        async def check() -> BaseException | None:
            if (e := ctx.should_stop()) is not None:
                return e
            return JobCancelled("cancelada") if await store.status_of(ctx.pool, run.id) in TERMINAL else None

        return check

    async def after_final(self, job: Job, status: str) -> None:
        target = JOB_TO_RUN.get(status)
        if target is None or self.sdk is None or self.sdk.pool is None:
            return
        output = job.result if target == DONE and isinstance(job.result, dict) else None
        error = (job.error or "erro desconhecido") if target == ERROR else None
        await store.finish_run(self.sdk.pool, run_id_of(job), target, output, error)

    def final_event(self, job: Job, status: str, detail: str) -> outbox.Event | None:
        # A cancel is audited by whoever cancelled it (one event per action).
        if status == "cancelled":
            return None
        action = FINAL_ACTION.get(JOB_TO_RUN.get(status, ""), "Execução encerrada")
        return outbox.audit(job.user, action, f"#{run_id_of(job)}", detail[:500])
