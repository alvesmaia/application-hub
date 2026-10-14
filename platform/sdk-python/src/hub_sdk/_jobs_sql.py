"""SQL of :mod:`hub_sdk.jobs` over ``hub_jobs`` (stable format: do not change without a migration).

Kept apart from the job model on purpose: every function takes a connection and returns raw rows
or affected-row counts, so this module has no import of :mod:`hub_sdk.jobs` (no import cycle).
"""

from __future__ import annotations

from typing import Any

from hub_sdk.schema import new_id

COLS = (
    "id, kind, status, payload, attempts, max_attempts, max_duration_s, checkpoint, progress_done, "
    "progress_total, progress_text, result, error, cancel_requested, user_name, "
    "(extract(epoch FROM created_at) * 1000)::int8 AS created_ms, "
    "(extract(epoch FROM updated_at) * 1000)::int8 AS updated_ms, "
    "(extract(epoch FROM finished_at) * 1000)::int8 AS finished_ms, "
    "extract(epoch FROM coalesce(finished_at, now()) - created_at)::float8 AS elapsed, "
    "(extract(epoch FROM run_after) * 1000)::int8 AS run_after_ms, attempt_token"
)

# Every write of an attempt only counts while it still owns the job (per-attempt fence).
_OWNED = "WHERE id = $1 AND attempt_token = $2 AND status = 'running'"


def affected(status: str) -> int:
    """Row count of an asyncpg command tag (``"UPDATE 3"`` → 3)."""
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0


async def insert_job(
    conn: Any, kind: str, payload: Any, key: str | None, attempts: int, secs: int, user: str
) -> Any:
    return await conn.fetchrow(
        "INSERT INTO hub_jobs (id, kind, payload, idempotency_key, max_attempts, max_duration_s, user_name) "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        "VALUES ($1, $2, $3, $4, $5, $6, $7) "
        "ON CONFLICT (kind, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING "
        f"RETURNING {COLS}",
        new_id("job"),
        kind,
        payload,
        key,
        attempts,
        secs,
        user,
    )


async def select_by_key(conn: Any, kind: str, key: str | None) -> Any:
    return await conn.fetchrow(
        f"SELECT {COLS} FROM hub_jobs WHERE kind = $1 AND idempotency_key = $2",  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        kind,
        key,
    )


async def select_one(conn: Any, job_id: str) -> Any:
    return await conn.fetchrow(f"SELECT {COLS} FROM hub_jobs WHERE id = $1", job_id)  # noqa: S608 -- SQL uses bound values and fixed internal fragments


async def select_recent(conn: Any, limit: int) -> list[Any]:
    return await conn.fetch(
        f"SELECT {COLS} FROM hub_jobs ORDER BY created_at DESC LIMIT $1",  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        limit,
    )


async def heartbeat_row(conn: Any, job_id: str, token: str, lease: float) -> Any:
    return await conn.fetchrow(
        "UPDATE hub_jobs SET lease_until = now() + make_interval(secs => $3), updated_at = now() "
        "WHERE id = $1 AND attempt_token = $2 AND status = 'running' RETURNING cancel_requested",
        job_id,
        token,
        float(lease),
    )


async def claim_row(conn: Any, kinds: list[str], owner: str, lease: float) -> Any:
    cols = COLS.replace("id, kind", "j.id, j.kind", 1)
    return await conn.fetchrow(
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
        owner,
        float(lease),
        new_id("att"),
    )


async def set_lease(conn: Any, job_id: str, token: str | None, lease: float) -> None:
    await conn.execute(
        "UPDATE hub_jobs SET lease_until = now() + make_interval(secs => $3) "
        "WHERE id = $1 AND attempt_token = $2",
        job_id,
        token,
        float(lease),
    )


async def reap_rows(conn: Any, kinds: list[str]) -> list[Any]:
    a = await conn.fetch(
        "UPDATE hub_jobs SET status = 'cancelled', finished_at = now(), updated_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        "lease_owner = NULL, lease_until = NULL, attempt_token = NULL, "
        "error = 'cancelada a pedido (o processo caiu antes de parar)' "
        "WHERE kind = ANY($1::text[]) AND status = 'running' AND lease_until < now() "
        f"AND cancel_requested RETURNING {COLS}",
        kinds,
    )
    b = await conn.fetch(
        "UPDATE hub_jobs SET status = 'dead', finished_at = now(), updated_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        "lease_owner = NULL, lease_until = NULL, attempt_token = NULL, "
        "error = coalesce(error || ' · ', '') || 'o processo caiu durante a última tentativa permitida' "
        "WHERE kind = ANY($1::text[]) AND status = 'running' AND lease_until < now() "
        f"AND attempts >= max_attempts RETURNING {COLS}",
        kinds,
    )
    return [*a, *b]


async def fail_killed_rows(conn: Any, job_ids: list[str], owner_prefix: str, reason: str) -> list[Any]:
    return await conn.fetch(
        "UPDATE hub_jobs SET status = 'failed', error = $3, finished_at = now(), updated_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        "lease_owner = NULL, lease_until = NULL, attempt_token = NULL "
        "WHERE id = ANY($1::text[]) AND status = 'running' AND starts_with(lease_owner, $2) "
        f"RETURNING {COLS}",
        job_ids,
        owner_prefix,
        reason,
    )


async def cancel_row(conn: Any, job_id: str) -> Any:
    now_cancel = (
        "(status = 'queued' OR (status = 'running' AND (lease_until IS NULL OR lease_until < now())))"
    )
    return await conn.fetchrow(
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


async def retry_row(conn: Any, job_id: str) -> Any:
    return await conn.fetchrow(
        "UPDATE hub_jobs SET status = 'queued', attempts = 0, run_after = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        "cancel_requested = false, error = NULL, finished_at = NULL, lease_owner = NULL, "
        "lease_until = NULL, attempt_token = NULL, updated_at = now() "
        f"WHERE id = $1 AND status IN ('failed', 'dead', 'cancelled') RETURNING {COLS}",
        job_id,
    )


async def requeue_shutdown(conn: Any, job_id: str, token: str) -> None:
    """Back to the queue without spending the attempt (graceful shutdown)."""
    await conn.execute(
        "UPDATE hub_jobs SET status = 'queued', attempts = greatest(attempts - 1, 0), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        "lease_owner = NULL, lease_until = NULL, attempt_token = NULL, run_after = now(), "
        f"updated_at = now() {_OWNED}",
        job_id,
        token,
    )


async def requeue_retry(conn: Any, job_id: str, token: str, detail: str, wait: float) -> None:
    await conn.execute(
        "UPDATE hub_jobs SET status = 'queued', error = $3, "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
        "run_after = now() + make_interval(secs => $4), updated_at = now(), "
        f"lease_owner = NULL, lease_until = NULL, attempt_token = NULL {_OWNED}",
        job_id,
        token,
        detail,
        float(wait),
    )


async def finish_done(conn: Any, job_id: str, token: str, outcome: Any) -> int:
    return affected(
        await conn.execute(
            "UPDATE hub_jobs SET status = 'done', result = $3, error = NULL, finished_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
            "updated_at = now(), lease_owner = NULL, lease_until = NULL, attempt_token = NULL, "
            f"progress_done = greatest(progress_done, progress_total) {_OWNED}",
            job_id,
            token,
            outcome,
        )
    )


async def finish_cancelled(conn: Any, job_id: str, token: str) -> int:
    return affected(
        await conn.execute(
            "UPDATE hub_jobs SET status = 'cancelled', error = 'cancelada a pedido', "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
            "finished_at = now(), updated_at = now(), lease_owner = NULL, lease_until = NULL, "
            f"attempt_token = NULL {_OWNED}",
            job_id,
            token,
        )
    )


# Only these two literals ever reach the SQL text (a KeyError for anything else).
_ERROR_SQL = {
    s: f"UPDATE hub_jobs SET status = '{s}', error = $3, finished_at = now(), "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
    f"updated_at = now(), lease_owner = NULL, lease_until = NULL, attempt_token = NULL {_OWNED}"
    for s in ("failed", "dead")
}


async def finish_with_error(conn: Any, status: str, job_id: str, token: str, detail: str) -> int:
    """Terminal ``failed``/``dead`` with ``detail`` as the error."""
    return affected(await conn.execute(_ERROR_SQL[status], job_id, token, detail))


async def save_checkpoint_row(
    conn: Any, job_id: str, token: str, checkpoint: Any, done: int, total: int, text: str, lease: float
) -> Any:
    return await conn.fetchrow(
        "UPDATE hub_jobs SET checkpoint = $3, progress_done = $4, progress_total = $5, progress_text = $6, "
        "lease_until = now() + make_interval(secs => $7), updated_at = now() "
        "WHERE id = $1 AND attempt_token = $2 AND status = 'running' RETURNING cancel_requested",
        job_id,
        token,
        checkpoint,
        done,
        total,
        text,
        lease,
    )


async def progress_count(conn: Any, job_id: str, token: str, done: int, total: int, text: str) -> int:
    return affected(
        await conn.execute(
            "UPDATE hub_jobs SET progress_done = $3, progress_total = $4, progress_text = $5, "
            "updated_at = now() WHERE id = $1 AND attempt_token = $2 AND status = 'running'",
            job_id,
            token,
            done,
            total,
            text,
        )
    )
