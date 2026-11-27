"""Runs in Postgres: create (row + durable job + audit in ONE transaction), start, finish (an
idempotent update that never leaves a terminal status), cancel, log, read and list."""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from typing import Any

from hub_sdk import outbox
from hub_sdk.jobs import Jobs, enqueue_with

from automations.model import (
    CANCELLED,
    JOB_SPEC,
    PROCESSING,
    RUN_COLS,
    TERMINAL,
    Process,
    Run,
    row_to,
    sources,
)

PAGE_SIZE = 25
PERIODS = (7, 30, 90)


@dataclass(frozen=True)
class Requester:
    name: str  # display name (shown)
    login: str  # stable login (who may see the run)


async def create_run(
    conn: Any, p: Process, params: dict[str, Any], trigger: str, who: Requester, trigger_id: int | None = None
) -> Run:
    """Run (pinned to the process's package version NOW) + job ``automations.run`` + audit event,
    with ``conn`` - the caller's transaction."""
    rid = await conn.fetchval(
        "INSERT INTO aut_runs (process_id, process_name, package_name, package_version, params, trigger, "
        "requested_by, requested_login, trigger_id, timeout_s) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING id",
        p.id, p.name, p.package_name, p.package_version, params, trigger, who.name, who.login, trigger_id,
        p.timeout_s,
    )  # fmt: skip
    job, _ = await enqueue_with(conn, JOB_SPEC, {"run_id": rid}, who.name)
    row = await conn.fetchrow(
        f"UPDATE aut_runs SET job_id = $2 WHERE id = $1 RETURNING {RUN_COLS}", rid, job.id
    )
    await outbox.write(
        conn, outbox.audit(who.name, "Execução solicitada", f"#{rid} · {p.name}", summary(params))
    )
    return row_to(Run, row)


def summary(params: dict[str, Any]) -> str:
    return json.dumps(params, ensure_ascii=False, default=str)[:500]


async def start_run(pool: Any, run_id: int) -> Run | None:
    """``pendente`` → ``processando`` (a retry finds it already ``processando``); ``None`` when the
    run is terminal (cancelled meanwhile) or gone - the job then has nothing to do."""
    async with pool.acquire() as c:
        row = await c.fetchrow(
            f"UPDATE aut_runs SET status = $2, started_at = coalesce(started_at, now()) "
            f"WHERE id = $1 AND NOT (status = ANY($3::text[])) RETURNING {RUN_COLS}",
            run_id,
            PROCESSING,
            sorted(TERMINAL),
        )
    return row_to(Run, row) if row is not None else None


async def finish_run(
    pool: Any, run_id: int, status: str, output: dict[str, Any] | None, error: str | None
) -> bool:
    """Moves the run to a terminal ``status``; ``False`` (nothing changes) if it is already terminal."""
    async with pool.acquire() as c:
        return await finish_with(c, run_id, status, output, error)


async def finish_with(
    conn: Any, run_id: int, status: str, output: dict[str, Any] | None, error: str | None
) -> bool:
    done = await conn.execute(
        "UPDATE aut_runs SET status = $2, output = $3, error = $4, finished_at = now(), "
        "duration_ms = (extract(epoch FROM now() - coalesce(started_at, created_at)) * 1000)::int "
        "WHERE id = $1 AND status = ANY($5::text[])",
        run_id,
        status,
        output,
        error,
        sources(status),
    )
    return str(done).endswith(" 1")


async def save_log(pool: Any, run_id: int, log: str) -> None:
    async with pool.acquire() as c:
        await c.execute("UPDATE aut_runs SET log = $2 WHERE id = $1", run_id, log.replace("\x00", ""))


async def status_of(pool: Any, run_id: int) -> str | None:
    async with pool.acquire() as c:
        status = await c.fetchval("SELECT status FROM aut_runs WHERE id = $1", run_id)
    return str(status) if status is not None else None


async def cancel_run(conn: Any, run: Run, user: str) -> bool:
    """``cancelado`` now (with its audit event, in the caller's transaction) if still
    ``pendente``/``processando``. The caller then asks the job to stop; the running job sees the
    terminal status and kills its subprocess."""
    ok = await finish_with(conn, run.id, CANCELLED, None, None)
    if ok:
        await outbox.write(conn, outbox.audit(user, "Execução cancelada", f"#{run.id}", run.process_name))
    return ok


async def stop_job(jobs: Jobs | None, run: Run) -> None:
    if jobs is not None and run.job_id is not None:
        with contextlib.suppress(LookupError, ValueError):
            await jobs.cancel(run.job_id)


async def get_run(pool: Any, run_id: int) -> Run | None:
    async with pool.acquire() as c:
        row = await c.fetchrow(f"SELECT {RUN_COLS} FROM aut_runs WHERE id = $1", run_id)
    return row_to(Run, row) if row is not None else None


@dataclass(frozen=True)
class RunFilter:
    process_id: int = 0  # 0 = all
    status: str = ""
    days: int = 30
    page: int = 1
    login: str = ""  # only this user's runs ("" = everyone's: admins)

    def where(self) -> tuple[str, list[Any]]:
        sql = (
            "WHERE ($1::bigint = 0 OR process_id = $1) AND ($2::text = '' OR status = $2) "
            "AND created_at >= now() - make_interval(days => $3) AND ($4::text = '' OR requested_login = $4)"
        )
        return sql, [self.process_id, self.status, self.days, self.login]


async def list_runs(pool: Any, f: RunFilter, limit: int = PAGE_SIZE) -> tuple[int, list[Run]]:
    where, args = f.where()
    async with pool.acquire() as c:
        total = await c.fetchval(f"SELECT count(*) FROM aut_runs {where}", *args)
        rows = await c.fetch(
            f"SELECT {RUN_COLS} FROM aut_runs {where} ORDER BY id DESC LIMIT {int(limit)} "
            f"OFFSET {(max(1, f.page) - 1) * int(limit)}",
            *args,
        )
    return int(total), [row_to(Run, r) for r in rows]


async def last_runs(pool: Any, login: str) -> dict[int, Run]:
    """The most recent run of each process (only ``login``'s when not empty)."""
    async with pool.acquire() as c:
        rows = await c.fetch(
            f"SELECT DISTINCT ON (process_id) {RUN_COLS} FROM aut_runs WHERE ($1::text = '' OR requested_login = $1) "
            "ORDER BY process_id, id DESC",
            login,
        )
    return {r["process_id"]: row_to(Run, r) for r in rows}
