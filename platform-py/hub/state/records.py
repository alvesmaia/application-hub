"""Expiring records and rate windows (tables `records` and `rate_windows`)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import asyncpg

from hub.state.model import Change, now_ms


class RecordOps:
    """Mixin of `StateDb`: needs only `self.pool`."""

    pool: asyncpg.Pool

    async def rec_put(self, kind: str, key: str, value: Any, expires_at: int) -> None:
        async with self.pool.acquire() as c:
            await c.execute(
                """INSERT INTO records(kind,key,value,expires_at,updated_at) VALUES($1,$2,$3::jsonb,$4,$5)
                ON CONFLICT(kind,key) DO UPDATE SET value=EXCLUDED.value,expires_at=EXCLUDED.expires_at,updated_at=EXCLUDED.updated_at""",
                kind,
                key,
                json.dumps(value),
                expires_at,
                now_ms(),
            )

    async def rec_insert_new(self, kind: str, key: str, value: Any, expires_at: int, now: int) -> bool:
        async with self.pool.acquire() as c:
            status = await c.execute(
                """INSERT INTO records(kind,key,value,expires_at,updated_at) VALUES($1,$2,$3::jsonb,$4,$5)
                ON CONFLICT(kind,key) DO UPDATE SET value=EXCLUDED.value,expires_at=EXCLUDED.expires_at,updated_at=EXCLUDED.updated_at
                WHERE records.expires_at <= $5 OR records.value IS NULL""",
                kind,
                key,
                json.dumps(value),
                expires_at,
                now,
            )
            return status.endswith("1")

    async def rec_get(self, kind: str, key: str, now: int) -> Any | None:
        async with self.pool.acquire() as c:
            value = await c.fetchval(
                "SELECT value FROM records WHERE kind=$1 AND key=$2 AND expires_at>$3 AND value IS NOT NULL",
                kind,
                key,
                now,
            )
            return json.loads(value) if value is not None else None

    async def rec_take(self, kind: str, key: str, now: int) -> Any | None:
        async with self.pool.acquire() as c:
            row = await c.fetchrow(
                "DELETE FROM records WHERE kind=$1 AND key=$2 RETURNING value,expires_at", kind, key
            )
            return json.loads(row[0]) if row and row[0] is not None and row[1] > now else None

    async def rec_delete(self, kind: str, key: str) -> bool:
        async with self.pool.acquire() as c:
            return (await c.execute("DELETE FROM records WHERE kind=$1 AND key=$2", kind, key)).endswith("1")

    async def rec_delete_where(self, kind: str, field: str, value: str) -> int:
        async with self.pool.acquire() as c:
            n = await c.execute("DELETE FROM records WHERE kind=$1 AND value->>$2=$3", kind, field, value)
            return int(n.rsplit(" ", 1)[1])

    async def rec_delete_key_suffix(self, kind: str, suffix: str) -> int:
        async with self.pool.acquire() as c:
            n = await c.execute(
                "DELETE FROM records WHERE kind=$1 AND right(key,length($2))=$2", kind, suffix
            )
            return int(n.rsplit(" ", 1)[1])

    async def rec_list(
        self, kind: str, now: int, limit: int, filter: tuple[str, str] | None = None
    ) -> list[tuple[str, Any, int]]:
        sql = "SELECT key,value,expires_at FROM records WHERE kind=$1 AND expires_at>$2 AND value IS NOT NULL"
        args: list[Any] = [kind, now]
        if filter:
            sql += " AND value->>$3=$4"
            args.extend(filter)
        sql += f" ORDER BY key LIMIT ${len(args) + 1}"
        args.append(limit)
        async with self.pool.acquire() as c:
            rows = await c.fetch(sql, *args)
        return [(r[0], json.loads(r[1]), r[2]) for r in rows]

    async def rec_count(self, kind: str, now: int, filter: tuple[str, str] | None = None) -> int:
        sql = "SELECT count(*) FROM records WHERE kind=$1 AND expires_at>$2 AND value IS NOT NULL"
        args: list[Any] = [kind, now]
        if filter:
            sql += " AND value->>$3=$4"
            args.extend(filter)
        async with self.pool.acquire() as c:
            return cast(int, await c.fetchval(sql, *args))

    async def rec_update(
        self, kind: str, key: str, now: int, fn: Callable[[Any | None], tuple[Change, Any]]
    ) -> Any:
        async with self.pool.acquire() as c, c.transaction():
            await c.execute(
                "INSERT INTO records(kind,key,value,expires_at,updated_at) VALUES($1,$2,NULL,0,0) ON CONFLICT DO NOTHING",
                kind,
                key,
            )
            row = await c.fetchrow(
                "SELECT value,expires_at FROM records WHERE kind=$1 AND key=$2 FOR UPDATE", kind, key
            )
            if row is None:
                raise RuntimeError("record placeholder disappeared")
            cur = json.loads(row[0]) if row[0] is not None and row[1] > now else None
            change, out = fn(cur)
            if change.action == "set":
                await c.execute(
                    "UPDATE records SET value=$3::jsonb,expires_at=$4,updated_at=$5 WHERE kind=$1 AND key=$2",
                    kind,
                    key,
                    json.dumps(change.value),
                    change.expires_at,
                    now,
                )
            elif change.action == "delete" or cur is None:
                await c.execute("DELETE FROM records WHERE kind=$1 AND key=$2", kind, key)
            return out

    async def rec_take_expired(self, kind: str, now: int, limit: int) -> list[tuple[str, Any]]:
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                """DELETE FROM records WHERE (kind,key) IN
                (SELECT kind,key FROM records WHERE kind=$1 AND expires_at<=$2 AND value IS NOT NULL ORDER BY expires_at LIMIT $3 FOR UPDATE SKIP LOCKED)
                RETURNING key,value""",
                kind,
                now,
                limit,
            )
        return [(r[0], json.loads(r[1])) for r in rows]

    async def rate_hit(
        self, bucket: str, key: str, window_ms: int, limit: int, now: int
    ) -> tuple[int, int] | tuple[None, int]:
        window_ms = max(1, window_ms)
        win, retry = divmod(now, window_ms)
        retry = (win + 1) * window_ms - now
        if limit <= 0:
            return None, retry
        async with self.pool.acquire() as c:
            n = cast(
                int | None,
                await c.fetchval(
                    """INSERT INTO rate_windows(bucket,key,win,n,expires_at) VALUES($1,$2,$3,1,$4)
                ON CONFLICT(bucket,key,win) DO UPDATE SET n=rate_windows.n+1 WHERE rate_windows.n<$5 RETURNING n""",
                    bucket,
                    key,
                    win,
                    (win + 1) * window_ms,
                    limit,
                ),
            )
        return (n, retry) if n is not None else (None, retry)

    async def rate_peek(self, bucket: str, key: str, window_ms: int, now: int) -> int:
        async with self.pool.acquire() as c:
            value = cast(
                int | None,
                await c.fetchval(
                    "SELECT n FROM rate_windows WHERE bucket=$1 AND key=$2 AND win=$3",
                    bucket,
                    key,
                    now // max(1, window_ms),
                ),
            )
            return value or 0

    async def rate_like(self, bucket: str, like: str, window_ms: int, now: int, clear: bool = False) -> int:
        """Adds (or, with `clear`, deletes) the counts of the current window for the `LIKE` keys (account unlock)."""
        async with self.pool.acquire() as c:
            if clear:
                await c.execute("DELETE FROM rate_windows WHERE bucket=$1 AND key LIKE $2", bucket, like)
                return 0
            n = await c.fetchval(
                "SELECT COALESCE(SUM(n),0) FROM rate_windows WHERE bucket=$1 AND key LIKE $2 AND win=$3",
                bucket,
                like,
                now // max(1, window_ms),
            )
            return int(n or 0)
