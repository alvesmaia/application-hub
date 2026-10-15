"""Hub background tasks (F5a): leadership across replicas, outbox collection, backup scheduler, sweeps and
Inbox reload across replicas. Turned on/off by the `lifespan` (`start(db)` / `stop()`)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import FastAPI

from hub.admin.store import OutboxIngest
from hub.api.mcp import sweep as mcp_sweep
from hub.ops import updates
from hub.ops.leader import Leader
from hub.ops.model import now_ms
from hub.ops.outbox import Outbox
from hub.ops.scheduler import Scheduler
from hub.ops.supervisor import Supervisor
from hub.state import StateDb


class Background:
    def __init__(self, app: FastAPI, client: Any, data_dir: Any) -> None:
        self.app, self.client, self.data_dir = app, client, data_dir
        self.parts: list[Any] = []
        self.watch: asyncio.Task[None] | None = None

    def say(self, level: str, msg: str) -> None:
        print(f"hub: {msg}", flush=True)
        self.app.state.admin.logs.add(level, "platform", "plataforma", f"[réplicas] {msg}", "hub")

    def _leader(self, db: StateDb | None) -> Leader:
        st = self.app.state
        st.leader = leader = Leader(db, self.say)
        st.insight.is_leader = leader.leading
        st.insight.on_alert = st.platform_alert
        st.insight.hub_jobs = st.ops_jobs.running_all
        return leader

    async def start(self, db: StateDb | None, outbox_every: float = 5) -> None:
        st = self.app.state
        st.ops_jobs.db = db
        st.ops_jobs.start()
        leader = self._leader(db)
        outbox = Outbox(self.app, self.client, leader, OutboxIngest(self.data_dir / "outbox"))
        sched = Scheduler(self.app, leader)
        st.ops_outbox, st.ops_scheduler = outbox, sched
        st.ops_supervisor = sup = Supervisor(self.app, leader)
        await st.ops_reconcile()
        leader.start(self.takeover, self.sweeps)
        outbox.start(outbox_every)
        sched.start()
        sup.start()
        self.parts = [sup, sched, outbox, leader]
        if updates.enabled():  # new version notice (optional)
            st.update_check = chk = updates.UpdateCheck()
            chk.start()
            self.parts.append(chk)
        if db:
            self.watch = asyncio.create_task(self._inbox_refresh())

    async def sweeps(self) -> None:
        """From the leader, every minute: expired records in the state database, expired requests/confirmations, orphan jobs."""
        st, db = self.app.state, self.app.state.auth.db
        st.leader.ran("sweeps")
        if db:
            await db.sweep(now_ms())
        await mcp_sweep(self.app)
        await st.ops_jobs.interrupt_dead()
        await self.recover_dead_restores()

    async def takeover(self) -> None:
        """On taking leadership: what the previous replica left behind."""
        await self.app.state.ops_jobs.interrupt_dead()
        await self.recover_dead_restores()

    async def recover_dead_restores(self) -> None:
        """Restore whose replica died midway: the app stays in maintenance, marked as failed, until someone checks."""
        st, db = self.app.state, self.app.state.auth.db
        if not db:
            return
        alive = {r[0] for r in await db.replicas(30_000)}
        async with st.ops.locked(True) as d:
            for k, mt in d["maintenance"].items():
                if mt.get("kind") == "restauracao" and mt.get("replica") not in alive:
                    mt.update(kind="restauracao_falhou", detail="O hub reiniciou durante a restauração: o estado dos "
                              "dados é incerto. Confira os dados (restaure o snapshot \"Pré-restauração\" ou outro backup "
                              "se preciso; um banco anterior pode ter ficado de lado — veja \"Bancos de lado\") e libere em Backups.")  # fmt: skip
                    self.say("WARN", f"restauração interrompida (réplica morta): {k}")

    async def _inbox_refresh(self) -> None:
        """Reloads the Inbox when another replica wrote `inbox.json` (wakes this one's SSE)."""
        inbox = self.app.state.inbox
        while True:
            await asyncio.sleep(0.5)
            with contextlib.suppress(Exception):
                inbox.reload_if_changed()

    async def stop(self) -> None:
        if self.watch:
            self.watch.cancel()
            await asyncio.gather(self.watch, return_exceptions=True)
        for p in self.parts:
            await p.stop()
        await self.app.state.ops_jobs.stop()
