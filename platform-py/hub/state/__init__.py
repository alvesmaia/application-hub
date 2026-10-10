"""Shared PostgreSQL runtime state, compatible with hub-state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import socket
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, cast

import asyncpg

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,47}$")
_MIGRATION = """
CREATE TABLE IF NOT EXISTS records (kind text NOT NULL, key text NOT NULL, value jsonb,
 expires_at bigint NOT NULL, updated_at bigint NOT NULL DEFAULT 0, PRIMARY KEY(kind,key));
CREATE INDEX IF NOT EXISTS records_expires ON records(expires_at);
CREATE TABLE IF NOT EXISTS sessions (id_hash text PRIMARY KEY, user_id text NOT NULL,
 created_at bigint NOT NULL, last_seen bigint NOT NULL, method text NOT NULL DEFAULT '', replica text NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS rate_windows (bucket text NOT NULL, key text NOT NULL, win bigint NOT NULL,
 n integer NOT NULL, expires_at bigint NOT NULL, PRIMARY KEY(bucket,key,win));
CREATE INDEX IF NOT EXISTS rate_windows_expires ON rate_windows(expires_at);
CREATE TABLE IF NOT EXISTS leases (name text PRIMARY KEY, holder text NOT NULL, fence bigint NOT NULL,
 expires_at bigint NOT NULL, acquired_at bigint NOT NULL, renewed_at bigint NOT NULL);
CREATE TABLE IF NOT EXISTS replicas (id text PRIMARY KEY, started_at bigint NOT NULL,
 heartbeat_at bigint NOT NULL, info jsonb NOT NULL DEFAULT '{}'::jsonb);
CREATE TABLE IF NOT EXISTS file_gen (name text PRIMARY KEY, gen bigint NOT NULL,
 replica text NOT NULL DEFAULT '', at bigint NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS jobs (id bigserial PRIMARY KEY, replica text NOT NULL DEFAULT '',
 done boolean NOT NULL DEFAULT false, data jsonb NOT NULL, updated_at bigint NOT NULL);
CREATE INDEX IF NOT EXISTS jobs_updated ON jobs(updated_at);
CREATE TABLE IF NOT EXISTS kv (k text PRIMARY KEY, v text NOT NULL);
"""
DB_NOW = "(extract(epoch from clock_timestamp()) * 1000)::bigint"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def default_replica_id() -> str:
    base = os.getenv("HUB_REPLICA_ID") or os.getenv("HOSTNAME") or socket.gethostname() or "hub"
    base = "".join(c for c in base if c.isascii() and (c.isalnum() or c in "-_."))[:48]
    return f"{base}-{secrets.token_hex(3)}"


@dataclass
class Config:
    url: str
    schema: str = "hub"
    pool: int = 8
    replica: str = ""

    def __post_init__(self) -> None:
        if not _IDENT.fullmatch(self.schema):
            raise ValueError(f"invalid schema: {self.schema}")
        if self.pool < 1:
            raise ValueError("pool must be positive")
        if not self.replica:
            self.replica = default_replica_id()

    @classmethod
    def from_env(cls) -> Config | None:
        url = os.getenv("HUB_STATE_DB_URL", "").strip()
        if not url:
            return None
        schema = os.getenv("HUB_STATE_DB_SCHEMA", "hub")
        if not _IDENT.fullmatch(schema):
            schema = "hub"
        try:
            pool = max(1, int(os.getenv("HUB_STATE_DB_POOL", "12")))
        except ValueError:
            pool = 12
        return cls(url, schema, pool)


@dataclass(frozen=True)
class Change:
    action: str
    value: Any = None
    expires_at: int = 0

    @classmethod
    def keep(cls) -> Change:
        return cls("keep")

    @classmethod
    def set(cls, value: Any, expires_at: int) -> Change:
        return cls("set", value, expires_at)

    @classmethod
    def delete(cls) -> Change:
        return cls("delete")


@dataclass(frozen=True)
class SessionRow:
    id_hash: str
    user_id: str
    created_at: int
    last_seen: int
    method: str = ""


@dataclass(frozen=True)
class SessionLimits:
    idle_ms: int
    absolute_ms: int
    max_per_user: int


@dataclass(frozen=True)
class LeaseInfo:
    holder: str
    fence: int
    expires_in_ms: int
    acquired_at: int


class StateDb:
    def __init__(self, pool: asyncpg.Pool, config: Config) -> None:
        self.pool, self.config = pool, config

    @classmethod
    async def connect(cls, config: Config, wait: float = 20) -> StateDb:
        if not _IDENT.fullmatch(config.schema):
            raise ValueError(f"invalid schema: {config.schema}")
        conn = await asyncpg.connect(config.url)
        try:
            await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{config.schema}"')
        finally:
            await conn.close()
        pool = await asyncpg.create_pool(
            config.url,
            min_size=1,
            max_size=max(2, config.pool),
            server_settings={"search_path": config.schema},
        )
        db = cls(pool, config)
        deadline = time.monotonic() + wait
        while True:
            try:
                await db._migrate()
                return db
            except asyncpg.PostgresError:
                if time.monotonic() >= deadline:
                    await pool.close()
                    raise
                await asyncio.sleep(2)

    async def close(self) -> None:
        await self.pool.close()

    async def _migrate(self) -> None:
        async with self.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('hub-state-migrate:' || $1::text))", self.config.schema
            )
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY, at bigint NOT NULL)"
            )
            exists = await conn.fetchval("SELECT 1 FROM schema_migrations WHERE version=1")
            if not exists:
                await conn.execute(_MIGRATION)
                await conn.execute(f"INSERT INTO schema_migrations VALUES(1,{DB_NOW})")

    @property
    def replica(self) -> str:
        return self.config.replica

    @property
    def schema(self) -> str:
        return self.config.schema

    async def db_now(self) -> int:
        async with self.pool.acquire() as c:
            return cast(int, await c.fetchval(f"SELECT {DB_NOW}"))

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
        """Soma (ou apaga, com `clear`) as contagens da janela atual das chaves `LIKE` (desbloqueio de conta)."""
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

    async def session_create(self, s: SessionRow, lim: SessionLimits, replace: str | None = None) -> None:
        async with self.pool.acquire() as c, c.transaction():
            await c.execute(
                "SELECT pg_advisory_xact_lock(hashtext('hub-session-user:'||$1::text))", s.user_id
            )
            if replace:
                await c.execute("DELETE FROM sessions WHERE id_hash=$1", replace)
            await c.execute(
                "DELETE FROM sessions WHERE user_id=$1 AND (last_seen<=$2::bigint-$3::bigint OR created_at<=$2::bigint-$4::bigint)",
                s.user_id,
                s.created_at,
                lim.idle_ms,
                lim.absolute_ms,
            )
            await c.execute(
                "DELETE FROM sessions WHERE user_id=$1 AND id_hash NOT IN (SELECT id_hash FROM sessions WHERE user_id=$1 ORDER BY last_seen DESC,created_at DESC LIMIT $2)",
                s.user_id,
                max(1, lim.max_per_user) - 1,
            )
            await c.execute(
                "INSERT INTO sessions(id_hash,user_id,created_at,last_seen,method,replica) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(id_hash) DO NOTHING",
                s.id_hash,
                s.user_id,
                s.created_at,
                s.last_seen,
                s.method,
                self.replica,
            )

    async def session_get(self, id_hash: str) -> SessionRow | None:
        async with self.pool.acquire() as c:
            r = await c.fetchrow(
                "SELECT id_hash,user_id,created_at,last_seen,method FROM sessions WHERE id_hash=$1", id_hash
            )
        return SessionRow(*r) if r else None

    async def session_touch(self, id_hash: str, now: int, min_step_ms: int) -> bool:
        async with self.pool.acquire() as c:
            return (
                await c.execute(
                    "UPDATE sessions SET last_seen=$2::bigint WHERE id_hash=$1 AND last_seen<=$2::bigint-$3::bigint",
                    id_hash,
                    now,
                    min_step_ms,
                )
            ).endswith("1")

    async def session_delete(self, id_hash: str) -> bool:
        async with self.pool.acquire() as c:
            return (await c.execute("DELETE FROM sessions WHERE id_hash=$1", id_hash)).endswith("1")

    async def session_delete_user(self, user_id: str) -> int:
        async with self.pool.acquire() as c:
            return int((await c.execute("DELETE FROM sessions WHERE user_id=$1", user_id)).rsplit(" ", 1)[1])

    async def session_count_user(self, user_id: str) -> int:
        async with self.pool.acquire() as c:
            return cast(int, await c.fetchval("SELECT count(*) FROM sessions WHERE user_id=$1", user_id))

    async def session_purge(self, now: int, idle_ms: int, absolute_ms: int) -> int:
        async with self.pool.acquire() as c:
            return int(
                (
                    await c.execute(
                        "DELETE FROM sessions WHERE last_seen<=$1::bigint-$2::bigint OR created_at<=$1::bigint-$3::bigint",
                        now,
                        idle_ms,
                        absolute_ms,
                    )
                ).rsplit(" ", 1)[1]
            )

    async def session_list(self, limit: int) -> list[SessionRow]:
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id_hash,user_id,created_at,last_seen,method FROM sessions ORDER BY last_seen DESC LIMIT $1",
                limit,
            )
        return [SessionRow(*r) for r in rows]

    async def lease_acquire(self, name: str, holder: str, ttl_ms: int) -> int | None:
        async with self.pool.acquire() as c:
            return cast(
                int | None,
                await c.fetchval(
                    """INSERT INTO leases(name,holder,fence,expires_at,acquired_at,renewed_at) VALUES($1,$2,1,(extract(epoch from clock_timestamp()) * 1000)::bigint+$3,(extract(epoch from clock_timestamp()) * 1000)::bigint,(extract(epoch from clock_timestamp()) * 1000)::bigint)
            ON CONFLICT(name) DO UPDATE SET fence=CASE WHEN leases.holder=EXCLUDED.holder AND leases.expires_at>(extract(epoch from clock_timestamp()) * 1000)::bigint THEN leases.fence ELSE leases.fence+1 END,
            acquired_at=CASE WHEN leases.holder=EXCLUDED.holder AND leases.expires_at>(extract(epoch from clock_timestamp()) * 1000)::bigint THEN leases.acquired_at ELSE EXCLUDED.acquired_at END,
            holder=EXCLUDED.holder,expires_at=EXCLUDED.expires_at,renewed_at=EXCLUDED.renewed_at
            WHERE leases.expires_at<=(extract(epoch from clock_timestamp()) * 1000)::bigint OR leases.holder=EXCLUDED.holder RETURNING fence""",
                    name,
                    holder,
                    ttl_ms,
                ),
            )

    async def lease_renew(self, name: str, holder: str, fence: int, ttl_ms: int) -> bool:
        async with self.pool.acquire() as c:
            return (
                await c.execute(
                    "UPDATE leases SET expires_at=(extract(epoch from clock_timestamp()) * 1000)::bigint+$4,renewed_at=(extract(epoch from clock_timestamp()) * 1000)::bigint WHERE name=$1 AND holder=$2 AND fence=$3 AND expires_at>(extract(epoch from clock_timestamp()) * 1000)::bigint",
                    name,
                    holder,
                    fence,
                    ttl_ms,
                )
            ).endswith("1")

    async def lease_release(self, name: str, holder: str, fence: int) -> bool:
        async with self.pool.acquire() as c:
            return (
                await c.execute(
                    "UPDATE leases SET expires_at=0 WHERE name=$1 AND holder=$2 AND fence=$3",
                    name,
                    holder,
                    fence,
                )
            ).endswith("1")

    async def lease_check(self, name: str, holder: str, fence: int) -> bool:
        async with self.pool.acquire() as c:
            return bool(
                await c.fetchval(
                    "SELECT 1 FROM leases WHERE name=$1 AND holder=$2 AND fence=$3 AND expires_at>(extract(epoch from clock_timestamp()) * 1000)::bigint",
                    name,
                    holder,
                    fence,
                )
            )

    async def lease_info(self, name: str) -> LeaseInfo | None:
        async with self.pool.acquire() as c:
            r = await c.fetchrow(
                "SELECT holder,fence,GREATEST(expires_at-(extract(epoch from clock_timestamp()) * 1000)::bigint,0),acquired_at FROM leases WHERE name=$1",
                name,
            )
        return LeaseInfo(*r) if r else None

    async def file_gens(self) -> dict[str, int]:
        async with self.pool.acquire() as c:
            return {r[0]: r[1] for r in await c.fetch("SELECT name,gen FROM file_gen")}

    @asynccontextmanager
    async def file_lock(self, name: str, write: bool = False) -> AsyncIterator[int]:
        async with self.pool.acquire() as c:
            await c.execute("SELECT pg_advisory_lock(hashtext('hub-file:'||$1::text))", name)
            try:
                await c.execute("INSERT INTO file_gen(name,gen) VALUES($1,0) ON CONFLICT DO NOTHING", name)
                seen = cast(int, await c.fetchval("SELECT gen FROM file_gen WHERE name=$1", name))
                if write:
                    await c.fetchval(
                        "UPDATE file_gen SET gen=gen+1,replica=$2,at=(extract(epoch from clock_timestamp()) * 1000)::bigint WHERE name=$1 RETURNING gen",
                        name,
                        self.replica,
                    )
                yield seen
            finally:
                await c.execute("SELECT pg_advisory_unlock(hashtext('hub-file:'||$1::text))", name)

    @asynccontextmanager
    async def dlock(self, name: str, shared: bool = False, wait: bool = True) -> AsyncIterator[bool]:
        async with self.pool.acquire() as c:
            fn = {
                (False, False): "pg_advisory_lock",
                (False, True): "pg_advisory_lock_shared",
                (True, False): "pg_try_advisory_lock",
                (True, True): "pg_try_advisory_lock_shared",
            }[(not wait, shared)]
            got = await c.fetchval(f"SELECT {fn}(hashtext('hub-lock:'||$1::text))", name)
            try:
                yield True if wait else bool(got)
            finally:
                if wait or got:
                    await c.execute(
                        f"SELECT pg_advisory_unlock{'_shared' if shared else ''}(hashtext('hub-lock:'||$1::text))",
                        name,
                    )

    async def job_floor(self, last: int) -> None:
        async with self.pool.acquire() as c:
            await c.execute(
                "SELECT setval(pg_get_serial_sequence('jobs','id'),GREATEST($1,(SELECT last_value FROM jobs_id_seq)),true)",
                max(1, last),
            )

    async def job_next_id(self) -> int:
        async with self.pool.acquire() as c:
            return cast(int, await c.fetchval("SELECT nextval(pg_get_serial_sequence('jobs','id'))"))

    async def job_put(self, id: int, data: Any, done: bool) -> None:
        async with self.pool.acquire() as c:
            await c.execute(
                "INSERT INTO jobs(id,replica,done,data,updated_at) VALUES($1,$2,$3,$4::jsonb,$5) ON CONFLICT(id) DO UPDATE SET done=EXCLUDED.done,data=EXCLUDED.data,updated_at=EXCLUDED.updated_at",
                id,
                self.replica,
                done,
                json.dumps(data),
                now_ms(),
            )

    async def job_get(self, id: int) -> Any | None:
        async with self.pool.acquire() as c:
            v = await c.fetchval("SELECT data FROM jobs WHERE id=$1", id)
        return json.loads(v) if v is not None else None

    async def job_list(self, limit: int) -> list[Any]:
        async with self.pool.acquire() as c:
            rows = await c.fetch("SELECT data FROM jobs ORDER BY id DESC LIMIT $1", limit)
        return [json.loads(r[0]) for r in rows]

    async def job_running_of(self, replica: str) -> list[Any]:
        async with self.pool.acquire() as c:
            rows = await c.fetch("SELECT data FROM jobs WHERE replica=$1 AND NOT done ORDER BY id", replica)
        return [json.loads(r[0]) for r in rows]

    async def job_owner(self, id: int) -> str | None:
        async with self.pool.acquire() as c:
            return cast(str | None, await c.fetchval("SELECT replica FROM jobs WHERE id=$1", id))

    async def kv_get_or_init(self, key: str, init: Callable[[], str]) -> str:
        value = init()
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO kv(k,v) VALUES($1,$2) ON CONFLICT DO NOTHING", key, value)
            return cast(str, await c.fetchval("SELECT v FROM kv WHERE k=$1", key))

    async def heartbeat(self, info: Any) -> None:
        async with self.pool.acquire() as c:
            await c.execute(
                f"INSERT INTO replicas(id,started_at,heartbeat_at,info) VALUES($1,{DB_NOW},{DB_NOW},$2::jsonb) ON CONFLICT(id) DO UPDATE SET heartbeat_at={DB_NOW},info=EXCLUDED.info",
                self.replica,
                json.dumps(info),
            )

    async def replicas(self, within_ms: int) -> list[tuple[str, int, int, Any]]:
        async with self.pool.acquire() as c:
            rows = await c.fetch(
                "SELECT id,started_at,heartbeat_at,info FROM replicas WHERE heartbeat_at>(extract(epoch from clock_timestamp()) * 1000)::bigint-$1 ORDER BY started_at",
                within_ms,
            )
        return [(r[0], r[1], r[2], json.loads(r[3])) for r in rows]

    async def replica_alive(self, id: str, within_ms: int) -> bool:
        async with self.pool.acquire() as c:
            return bool(
                await c.fetchval(
                    "SELECT 1 FROM replicas WHERE id=$1 AND heartbeat_at>(extract(epoch from clock_timestamp()) * 1000)::bigint-$2",
                    id,
                    within_ms,
                )
            )

    async def sweep(self, now: int) -> int:
        async with self.pool.acquire() as c:
            n = 0
            for sql, args in [
                ("DELETE FROM records WHERE expires_at<=$1", (now,)),
                ("DELETE FROM rate_windows WHERE expires_at<=$1", (now,)),
                (
                    "DELETE FROM replicas WHERE heartbeat_at<(extract(epoch from clock_timestamp()) * 1000)::bigint-86400000",
                    (),
                ),
                ("DELETE FROM jobs WHERE done AND updated_at<$1::bigint-604800000", (now,)),
            ]:
                status = await c.execute(sql, *args)
                n += int(status.rsplit(" ", 1)[1])
            return n


class Lease:
    """Background lease renewal with the same 80% local fencing margin as Rust."""

    def __init__(self, db: StateDb, name: str, holder: str, ttl_ms: int) -> None:
        self.db, self.name, self.holder = db, name, holder
        self.ttl_ms = max(300, ttl_ms)
        self._fence: int | None = None
        self._valid_until = 0.0
        self._stopped = False
        self._task = asyncio.create_task(self._run())

    @classmethod
    def spawn(cls, db: StateDb, name: str, ttl_ms: int, holder: str | None = None) -> Lease:
        return cls(db, name, holder or db.replica, ttl_ms)

    @property
    def is_leader(self) -> bool:
        return self._fence is not None and time.monotonic() < self._valid_until

    @property
    def fence(self) -> int | None:
        return self._fence if self.is_leader else None

    async def _run(self) -> None:
        every = self.ttl_ms / 3000
        while not self._stopped:
            started = time.monotonic()
            if self._fence is not None and started >= self._valid_until:
                self._fence = None
            try:
                if self._fence is None:
                    fence = await self.db.lease_acquire(self.name, self.holder, self.ttl_ms)
                    if fence is not None:
                        self._fence = fence
                        self._valid_until = started + self.ttl_ms / 1000 * 0.8
                elif await self.db.lease_renew(self.name, self.holder, self._fence, self.ttl_ms):
                    self._valid_until = started + self.ttl_ms / 1000 * 0.8
                else:
                    self._fence = None
            except asyncpg.PostgresError:
                pass
            await asyncio.sleep(every)

    async def release(self) -> None:
        self._stopped = True
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        if self._fence is not None:
            await self.db.lease_release(self.name, self.holder, self._fence)
        self._fence = None

    async def abandon(self) -> None:
        self._stopped = True
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._fence = None


__all__ = [
    "Change",
    "Config",
    "Lease",
    "LeaseInfo",
    "SessionLimits",
    "SessionRow",
    "StateDb",
    "default_replica_id",
    "now_ms",
    "sha256_hex",
]
