import asyncio

import pytest

from hub_sdk.heavy import Heavy
from hub_sdk.jobs import JobSpec, backoff
from hub_sdk.outbox import AUDIT, Outbox, audit
from hub_sdk.schema import ct_eq, new_id
from hub_sdk.shutdown import Shutdown


def test_heavy_rejects_beyond_max_and_releases() -> None:
    h = Heavy(2)
    a = h.try_acquire()
    b = h.try_acquire()
    assert a and b and h.try_acquire() is None and h.in_use == 2
    a.release()
    a.release()  # inócuo
    assert h.in_use == 1
    assert h.try_acquire() is not None


async def test_shutdown_wakes_waiters_and_drains() -> None:
    s = Shutdown()
    g = s.track()
    w = asyncio.create_task(s.wait())
    await asyncio.sleep(0)
    assert not s.triggered
    s.trigger()
    await asyncio.wait_for(w, 1)
    assert not await s.drain(0.1)
    g.done()
    assert await s.drain(0.1)


def test_backoff_and_spec() -> None:
    assert backoff(1, 2, 60, 0) == 1
    assert backoff(2, 2, 60, 0) == 2
    assert backoff(3, 2, 60, 0) == 4
    assert backoff(3, 2, 60, 0.999) < 8
    assert backoff(3, 2, 60, 0.5) > 4
    assert backoff(20, 2, 60, 0.999) <= 60
    assert backoff(20, 2, 60, 0) == 30
    assert JobSpec("x", max_attempts=0).max_attempts == 1


def test_ids_and_constant_time() -> None:
    a, b = new_id("evt"), new_id("evt")
    assert a.startswith("evt-") and len(a) == 36 and a != b
    assert ct_eq("abc", "abc") and not ct_eq("abc", "abd") and not ct_eq("abc", "abcd")


async def test_audit_trim_and_memory_outbox() -> None:
    e = audit("ana", "a" * 100, "alvo\n", "d\x07x")
    assert len(e.action) == 80 and e.target == "alvo" and e.detail == "dx" and e.kind == AUDIT
    o = Outbox.memory()
    a = await o.emit(audit("u", "A"))
    b = await o.emit(audit("u", "B"))
    p = await o.pending(0, 10)
    assert [x.action for x in p] == ["A", "B"]
    assert len(await o.pending(p[0].id, 10)) == 1
    assert await o.ack([a or ""]) == 1
    assert await o.ack([a or ""]) == 0
    assert [x.event_id for x in await o.pending(0, 10)] == [b]


def test_pool_sizes_respect_the_role_connection_budget(monkeypatch: "pytest.MonkeyPatch") -> None:
    from hub_sdk.db import pool_size

    for v in ("HUB_DB_POOL", "UVICORN_WORKERS"):
        monkeypatch.delenv(v, raising=False)
    assert pool_size("HUB_DB_POOL", 12, per_web_process=True) == 12
    monkeypatch.setenv("UVICORN_WORKERS", "2")
    assert pool_size("HUB_DB_POOL", 12, per_web_process=True) == 6
    assert pool_size("HUB_JOBS_POOL", 5) == 5  # jobs process: single
    monkeypatch.setenv("UVICORN_WORKERS", "8")
    assert pool_size("HUB_DB_POOL", 12, per_web_process=True) == 2
    monkeypatch.setenv("HUB_DB_POOL", "20")
    assert pool_size("HUB_DB_POOL", 12, per_web_process=True) == 20  # explicit wins
    # web 12 + 12 + 2 and jobs 4 + 5 fit within the app role's CONNECTION LIMIT 40
    assert 12 + 12 + 2 + 4 + 5 <= 40
