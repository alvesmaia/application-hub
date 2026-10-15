"""Leadership across replicas (`hub-leader` lease of `hub-state`, database clock, fence): only the leader runs what must
be unique (outbox collection, backup scheduler, sweeps, alerts). Without a state database, a single replica is always the leader."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from hub.state import Lease, StateDb

LEADER_LEASE = "hub-leader"
Say = Callable[[str, str], None]
Hook = Callable[[], Awaitable[None]]


class Leader:
    def __init__(self, db: StateDb | None, say: Say | None = None) -> None:
        self.db, self.say = db, say or (lambda lvl, msg: print(f"hub: {msg}", flush=True))
        self.lease: Lease | None = None
        self.tasks: list[asyncio.Task[Any]] = []
        self.started = int(time.time() * 1000)
        self.was = False
        self.runs: dict[str, int] = {}  # leader tasks run by this replica (by name)

    @property
    def replica(self) -> str:
        return self.db.replica if self.db else "unica"

    def leading(self) -> bool:
        return True if self.db is None else bool(self.lease and self.lease.is_leader)

    async def checked(self) -> bool:
        """`leading()` + fence checked in the database (before any action that touches shared state)."""
        if self.db is None:
            return True
        fence = self.lease.fence if self.lease else None
        return fence is not None and await self.db.lease_check(LEADER_LEASE, self.replica, fence)

    def ran(self, name: str) -> None:
        self.runs[name] = self.runs.get(name, 0) + 1

    def start(self, takeover: Hook, sweeps: Hook) -> None:
        """Inside the event loop: starts the lease, the liveness signal, the change watcher and the 60 s sweep."""
        if self.db is None:
            self.was = True
            self.tasks = [asyncio.create_task(self._every(60, sweeps))]
            return
        ttl = max(3, min(300, int(os.getenv("HUB_LEADER_TTL_S", "15") or 15))) * 1000
        self.lease = Lease.spawn(self.db, LEADER_LEASE, ttl)
        self.tasks = [
            asyncio.create_task(self._watch(takeover)),
            asyncio.create_task(self._heartbeat()),
            asyncio.create_task(self._every(60, sweeps)),
        ]

    async def _watch(self, takeover: Hook) -> None:
        while True:
            now = self.leading()
            if now != self.was:
                self.was = now
                await self._changed(now, takeover)
            await asyncio.sleep(0.5)

    async def _changed(self, now: bool, takeover: Hook) -> None:
        if not now:
            self.say("INFO", f"réplica {self.replica} deixou a liderança")
            return
        self.say("INFO", f"réplica {self.replica} assumiu a liderança (fence {self.lease and self.lease.fence}): "
                 "coleta do outbox, agendador de backups, varreduras e alertas")  # fmt: skip
        with contextlib.suppress(Exception):
            await takeover()

    async def _heartbeat(self) -> None:
        while True:
            try:
                if self.db:
                    await self.db.heartbeat({"started_at": self.started, "leader": self.leading()})
            except (asyncpg.PostgresError, OSError) as e:
                self.say("WARN", f"sinal de vida no banco de estado: {e}")
            await asyncio.sleep(5)

    async def _every(self, secs: float, fn: Hook) -> None:
        while True:
            await asyncio.sleep(secs)
            if await self.checked():
                try:
                    await fn()
                except Exception as e:
                    self.say("WARN", f"tarefa periódica do líder falhou: {type(e).__name__}: {e}")

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        for t in self.tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        if self.lease:
            await self.lease.release()  # graceful end: another replica takes over immediately

    async def info(self) -> dict[str, Any]:
        li = await self.db.lease_info(LEADER_LEASE) if self.db else None
        reps = await self.db.replicas(30_000) if self.db else []
        return {
            "replica": self.replica, "shared": self.db is not None, "leader": self.leading(),
            "holder": li.holder if li else self.replica, "fence": li.fence if li else 0,
            "replicas": [{"id": r[0], "started_at": r[1], "heartbeat_at": r[2],
                          "leader": bool(r[3].get("leader"))} for r in reps],
            "runs": self.runs,
        }  # fmt: skip
