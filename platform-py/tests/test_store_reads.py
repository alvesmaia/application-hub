"""Reading admin.json / auth.json takes no lock: writes are atomic (`os.replace`), so a reader always sees a whole
file. Before, every read took a cross-replica Postgres lock (4 round trips) and queued behind the others - about
six of them on every authenticated request. Writes still take both locks."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from hub.admin.store import AdminStore
from hub.auth import AuthStore


class CountingDb:
    """Named fake of the state database: counts the cross-replica file locks taken."""

    def __init__(self) -> None:
        self.locks: list[tuple[str, bool]] = []

    @asynccontextmanager
    async def file_lock(self, name: str, write: bool = False) -> AsyncIterator[int]:
        self.locks.append((name, write))
        yield 0


@pytest.mark.parametrize("make", [lambda p: AdminStore(p), lambda p: AuthStore(p / "auth.json")])
async def test_reads_take_no_lock_and_writes_take_it(tmp_path: Path, make: Any) -> None:
    store = make(tmp_path)
    db = CountingDb()
    store.db = db
    async with store.locked() as d:
        assert isinstance(d, dict)
    assert db.locks == []
    async with store.locked(True) as d:
        d.setdefault("probe", 1)
    assert [w for _, w in db.locks] == [True]
    async with store.locked() as d:
        assert d.get("probe") == 1  # the write is visible to the next read
