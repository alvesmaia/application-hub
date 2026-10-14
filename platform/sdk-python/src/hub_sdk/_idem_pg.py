"""Postgres helpers of :mod:`hub_sdk.idempotency`: the key's session advisory lock and the answers
read from an existing ``hub_idempotency`` record (stable format: do not change without a migration)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from hub_sdk._idem_io import (
    Send,
    _already_done,
    _in_progress,
    _mismatch,
    _row_stored,
    _send_stored,
    _unavailable,
)

SELECT = (
    "SELECT request_hash, state, status, headers, body FROM hub_idempotency "
    "WHERE route = $1 AND key = $2 AND expires_at > now()"
)


async def _pg_lock(
    wait: float, conn: Any, send: Send, lock_key: str, route: str, key: str, digest: str
) -> bool | None:
    """Waits up to ``wait`` seconds for the key's advisory lock. ``True`` = ours; ``False`` = already
    answered (replay, 409, 422); ``None`` = the connection broke (answered 503)."""
    deadline = time.monotonic() + wait
    while True:
        try:
            got = await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1, 7))", lock_key)
        except Exception as e:
            await _send_stored(send, _unavailable(e), False)
            return None
        if got:
            return True
        if await _pg_answer_waiting(conn, send, route, key, digest):
            return False
        if time.monotonic() >= deadline:
            await _send_stored(send, _in_progress(), False)
            return False
        await asyncio.sleep(0.1)


async def _pg_answer_waiting(conn: Any, send: Send, route: str, key: str, digest: str) -> bool:
    """While another request holds the key: answer from its record once that is final.
    Any failure here (including sending) only means "keep waiting"."""
    with contextlib.suppress(Exception):
        r = await conn.fetchrow(SELECT, route, key)
        if r is not None and r["request_hash"] != digest:
            await _send_stored(send, _mismatch(), False)
            return True
        if r is not None and r["state"] == "done":
            await _send_stored(send, _row_stored(r), True)
            return True
    return False


async def _pg_answer_existing(send: Send, existing: Any, digest: str) -> bool:
    if existing["request_hash"] != digest:
        await _send_stored(send, _mismatch(), False)
        return True
    if existing["state"] == "done":
        await _send_stored(send, _row_stored(existing), True)
        return True
    if existing["state"] == "committed":
        done, rep = _already_done()
        await _send_stored(send, done, rep)
        return True
    return False  # pending with no owner: the previous one died before committing


async def _pg_release(pool: Any, conn: Any, lock_key: str, locked: bool, healthy: bool) -> None:
    """Unlocks and returns the connection; one in doubt (broken or unlock failed) is terminated,
    so a session lock can never outlive us."""
    ok = healthy
    if locked and ok:
        try:
            await asyncio.shield(conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 7))", lock_key))
        except BaseException:
            ok = False
    if not ok:
        conn.terminate()
    with contextlib.suppress(Exception):
        await asyncio.shield(pool.release(conn))
