"""Session window: 24 h without use ends it; browsing renews it (at most once per 60 min); 3 days after the login it
ends anyway, renewed or not."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, nullcontext
from pathlib import Path
from typing import Any

import pytest

import hub.auth as auth
from hub.auth import AuthStore
from hub.state import SessionRow

HOUR = 3_600_000
USER = {"id": "u1", "email": "a@example.test", "name": "A", "roles": [], "active": True, "created_at": 1}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000_000_000

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(auth, "now_ms", c)
    return c


async def _store(tmp_path: Path) -> AuthStore:
    store = AuthStore(tmp_path / "auth.json")
    async with store.locked(True) as d:
        d["users"].append(dict(USER))
    return store


def test_defaults() -> None:
    assert auth.IDLE_MS == 24 * HOUR
    assert auth.RENEW_MS == HOUR
    assert auth.ABSOLUTE_MS == 72 * HOUR


async def test_unused_for_24h_ends(tmp_path: Path, clock: Clock) -> None:
    store = await _store(tmp_path)
    cookie = await store.session_new(USER)
    clock.now += 24 * HOUR
    assert await store.session(cookie) is None


async def test_browsing_renews_until_three_days(tmp_path: Path, clock: Clock) -> None:
    store = await _store(tmp_path)
    cookie = await store.session_new(USER)
    for _ in range(71):  # one visit per hour keeps it alive well past 24 h
        clock.now += HOUR
        assert await store.session(cookie) is not None
    clock.now += HOUR  # 72 h after the login
    assert await store.session(cookie) is None


async def test_renewal_happens_at_most_once_per_hour(tmp_path: Path, clock: Clock) -> None:
    store = await _store(tmp_path)
    cookie = await store.session_new(USER)
    clock.now += 30 * 60_000
    assert await store.session(cookie)  # 30 min: not renewed yet
    clock.now += 23 * HOUR + 31 * 60_000  # 24h01 since the last renewal (login)
    assert await store.session(cookie) is None


class FakeSessionDb:
    """Named fake of the `StateDb` session calls."""

    def __init__(self, row: SessionRow) -> None:
        self.row, self.touched = row, []

    async def session_get(self, _h: str) -> SessionRow:
        return self.row

    async def session_touch(self, _h: str, now: int, step: int) -> bool:
        self.touched.append((now, step))
        return True

    async def session_delete(self, _h: str) -> bool:
        return True

    def file_lock(self, _name: str, write: bool = False) -> AbstractAsyncContextManager[None]:
        return nullcontext()


async def test_db_session_renews_with_the_hour_step(tmp_path: Path, clock: Clock) -> None:
    db: Any = FakeSessionDb(SessionRow("h", "u1", clock.now, clock.now, "password"))
    store = await _store(tmp_path)
    store.db = db  # users stay in the file; only the session calls go to the fake
    clock.now += 59 * 60_000
    assert await store.session("x")
    assert db.touched == []
    clock.now += 60_000
    assert await store.session("x")
    assert db.touched == [(clock.now, HOUR)]
