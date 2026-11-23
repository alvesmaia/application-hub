"""Triggers (schedules of a process) in Postgres and the exactly-once fire (:func:`fire_due`)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hub_sdk import outbox

from automacoes.model import (
    PROCESS_COLS,
    SCHEDULER_USER,
    TRIGGER_COLS,
    TRIGGER_FROM,
    Process,
    Trigger,
    row_to,
)
from automacoes.schedule_rules import ScheduleIn, next_run
from automacoes.store import Requester, create_run

FIRE_BATCH = 20
SCHEDULER = Requester(SCHEDULER_USER, SCHEDULER_USER)


def rule_of(g: Trigger) -> ScheduleIn:
    return ScheduleIn.model_validate({"kind": g.kind, "every_minutes": g.every_minutes, "at_time": g.at_time})


async def fire_due(pool: Any, now: datetime) -> list[int]:
    """In ONE transaction: locks the due enabled triggers (``FOR UPDATE SKIP LOCKED``), creates
    each one's run + job, and moves ``next_run_at`` past ``now``. Another process (or replica)
    either skips the locked rows or, once this commits, re-checks them and no longer sees them as
    due — so each slot fires exactly once. Returns the ids of the runs created."""
    created: list[int] = []
    async with pool.acquire() as c, c.transaction():
        rows = await c.fetch(
            f"SELECT {TRIGGER_COLS} FROM {TRIGGER_FROM} WHERE g.enabled AND g.next_run_at <= $1 "
            f"ORDER BY g.next_run_at LIMIT {FIRE_BATCH} FOR UPDATE OF g SKIP LOCKED",
            now,
        )
        for g in (row_to(Trigger, r) for r in rows):
            run_id = await _fire(c, g)
            await c.execute(
                "UPDATE aut_triggers SET next_run_at = $2, last_run_at = $3, "
                "last_run_id = coalesce($4, last_run_id) WHERE id = $1",
                g.id,
                next_run(rule_of(g), now),
                now,
                run_id,
            )
            created += [run_id] if run_id is not None else []
    return created


async def _fire(conn: Any, g: Trigger) -> int | None:
    row = await conn.fetchrow(f"SELECT {PROCESS_COLS} FROM aut_processes WHERE id = $1", g.process_id)
    p = row_to(Process, row)
    if not p.enabled:  # a disabled process: the slot passes without a run
        return None
    run = await create_run(conn, p, p.params | g.params, "agendamento", SCHEDULER, g.id)
    return run.id


async def list_triggers(pool: Any) -> list[Trigger]:
    async with pool.acquire() as c:
        rows = await c.fetch(f"SELECT {TRIGGER_COLS} FROM {TRIGGER_FROM} ORDER BY g.id")
    return [row_to(Trigger, r) for r in rows]


async def get_trigger(pool: Any, tid: int) -> Trigger | None:
    async with pool.acquire() as c:
        row = await c.fetchrow(f"SELECT {TRIGGER_COLS} FROM {TRIGGER_FROM} WHERE g.id = $1", tid)
    return row_to(Trigger, row) if row is not None else None


async def save_trigger(
    conn: Any,
    tid: int | None,
    process_id: int,
    params: dict[str, Any],
    rule: ScheduleIn,
    user: str,
    now: datetime,
) -> int:
    """Creates (``tid`` None, disabled) or updates; ``next_run_at`` is recomputed from ``now``."""
    args = (process_id, params, rule.kind, rule.every_minutes, rule.at_time, next_run(rule, now))
    if tid is None:
        sql = (
            "INSERT INTO aut_triggers (process_id, params, kind, every_minutes, at_time, next_run_at, created_by) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id"
        )
        return int(await conn.fetchval(sql, *args, user))
    await conn.execute(
        "UPDATE aut_triggers SET process_id = $2, params = $3, kind = $4, every_minutes = $5, at_time = $6, "
        "next_run_at = $7 WHERE id = $1",
        tid,
        *args,
    )
    return tid


async def set_enabled(conn: Any, g: Trigger, enabled: bool, now: datetime) -> None:
    """Disabling stops future fires; enabling recomputes the next fire from ``now``."""
    await conn.execute(
        "UPDATE aut_triggers SET enabled = $2, next_run_at = $3 WHERE id = $1",
        g.id,
        enabled,
        next_run(rule_of(g), now) if enabled else g.next_run_at,
    )


async def delete_trigger(conn: Any, tid: int) -> bool:
    return str(await conn.execute("DELETE FROM aut_triggers WHERE id = $1", tid)).endswith(" 1")


async def audit(conn: Any, user: str, action: str, tid: int, process_name: str, detail: str = "") -> None:
    await outbox.write(conn, outbox.audit(user, action, f"gatilho #{tid} · {process_name}", detail))
