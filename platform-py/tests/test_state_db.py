import asyncio
import os
import secrets
from collections.abc import AsyncIterator

import asyncpg
import pytest

from hub.state import Change, Config, Lease, SessionLimits, SessionRow, StateDb, sha256_hex

DB_URL = os.getenv("HUB_DB_TEST_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="HUB_DB_TEST_URL not set")


@pytest.fixture
async def db() -> AsyncIterator[StateDb]:
    assert DB_URL
    schema = f"t_{secrets.token_hex(6)}"
    result = await StateDb.connect(Config(DB_URL, schema, 24, "test-a"))
    try:
        yield result
    finally:
        await result.close()
        conn = await asyncpg.connect(DB_URL)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await conn.close()


async def test_single_use_records_and_atomic_update(db: StateDb) -> None:
    now = 1_000_000
    key = sha256_hex("cnf_segredo")
    await db.rec_put("confirm", key, {"who": "u1"}, now + 60_000)
    values = await asyncio.gather(*(db.rec_take("confirm", key, now) for _ in range(32)))
    assert sum(v is not None for v in values) == 1
    await db.rec_put("confirm", "expired", 1, now + 10)
    assert await db.rec_get("confirm", "expired", now + 10) is None
    assert await db.rec_take("confirm", "expired", now + 10) is None
    assert await db.rec_insert_new("x", "k", 1, now + 5, now)
    assert not await db.rec_insert_new("x", "k", 2, now + 5, now)
    assert await db.rec_insert_new("x", "k", 3, now + 50, now + 6)

    async def increment(_: int) -> None:
        def update(value: object) -> tuple[Change, None]:
            return Change.set((value if isinstance(value, int) else 0) + 1, now + 60_000), None

        await db.rec_update("ctr", "k", now, update)

    await asyncio.gather(*(increment(i) for i in range(40)))
    assert await db.rec_get("ctr", "k", now) == 40
    await db.rec_update("ctr", "empty", now, lambda _: (Change.keep(), None))
    assert len(await db.rec_list("ctr", 0, 10)) == 1


async def test_fixed_window_rate_limit_is_atomic(db: StateDb) -> None:
    now = 120_000 * 7 + 5
    results = await asyncio.gather(*(db.rate_hit("api", "u:1", 60_000, 100, now) for _ in range(256)))
    assert sum(n is not None for n, _ in results) == 100
    assert await db.rate_peek("api", "u:1", 60_000, now) == 100
    assert (await db.rate_hit("api", "u:1", 60_000, 100, now + 60_000))[0] == 1


async def test_session_cap_and_lease_fencing(db: StateDb) -> None:
    assert DB_URL
    other = await StateDb.connect(Config(DB_URL, db.schema, 12, "test-b"))
    try:
        limits = SessionLimits(1_800_000, 43_200_000, 3)
        now = 10_000_000
        await asyncio.gather(
            *(
                (db if i % 2 == 0 else other).session_create(
                    SessionRow(f"h{i}", "u1", now + i, now + i, "password"), limits
                )
                for i in range(12)
            )
        )
        assert await db.session_count_user("u1") == 3
        first = await db.lease_acquire("leader", "A", 250)
        assert first == 1
        assert await other.lease_acquire("leader", "B", 250) is None
        await asyncio.sleep(0.3)
        second = await other.lease_acquire("leader", "B", 2000)
        assert second == 2
        assert not await db.lease_check("leader", "A", first)
        assert not await db.lease_renew("leader", "A", first, 1000)
        assert await other.lease_check("leader", "B", second)
        info = await other.lease_info("leader")
        assert info is not None and info.holder == "B"
    finally:
        await other.close()


async def test_file_locks_distributed_locks_jobs_kv_replicas(db: StateDb) -> None:
    async with db.file_lock("counter", write=True):
        assert (await db.file_gens())["counter"] == 1
    async with db.file_lock("counter"):
        assert (await db.file_gens())["counter"] == 1
    async with db.dlock("app:ativos"), db.dlock("app:ativos", wait=False) as got:
        assert not got
    async with db.dlock("app:backup", shared=True), db.dlock("app:backup", shared=True, wait=False) as got:
        assert got
    await db.job_floor(41)
    job = await db.job_next_id()
    assert job > 41
    await db.job_put(job, {"id": job, "done": False}, False)
    assert await db.job_get(job) == {"id": job, "done": False}
    assert len(await db.job_running_of(db.replica)) == 1
    await db.job_put(job, {"id": job, "done": True}, True)
    assert not await db.job_running_of(db.replica)
    assert (await db.kv_get_or_init("sig", lambda: "one")) == "one"
    assert (await db.kv_get_or_init("sig", lambda: "two")) == "one"
    await db.heartbeat({"v": 1})
    assert await db.replica_alive(db.replica, 5000)
    lease = Lease.spawn(db, "background-leader", 500)
    try:
        for _ in range(30):
            if lease.is_leader:
                break
            await asyncio.sleep(0.05)
        assert lease.is_leader and lease.fence is not None
    finally:
        await lease.release()


async def test_rate_like_counts_and_clears_account_buckets(db: StateDb) -> None:
    now = 5_000_000
    for key in ("acct:1.1.1.1|ana@x", "acct:2.2.2.2|ana@x", "acct:1.1.1.1|bia@x"):
        await db.rate_hit("login", key, 900_000, 10, now)
    assert await db.rate_like("login", "acct:%|ana@x", 900_000, now) == 2
    assert await db.rate_like("login", "acct:%|bia@x", 900_000, now) == 1
    await db.rate_like("login", "acct:%|ana@x", 0, 0, clear=True)
    assert await db.rate_like("login", "acct:%|ana@x", 900_000, now) == 0
    assert await db.rate_like("login", "acct:%|bia@x", 900_000, now) == 1
