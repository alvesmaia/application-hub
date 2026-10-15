"""Leadership lease kept alive in the background."""

from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from hub.state import StateDb


class Lease:
    """Background lease renewal, with the same 80% local fencing margin."""

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
            with suppress(asyncpg.PostgresError):
                await self._acquire_or_renew(started)
            await asyncio.sleep(every)

    async def _acquire_or_renew(self, started: float) -> None:
        if self._fence is None:
            fence = await self.db.lease_acquire(self.name, self.holder, self.ttl_ms)
            if fence is not None:
                self._fence = fence
                self._valid_until = started + self.ttl_ms / 1000 * 0.8
        elif await self.db.lease_renew(self.name, self.holder, self._fence, self.ttl_ms):
            self._valid_until = started + self.ttl_ms / 1000 * 0.8
        else:
            self._fence = None

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
