"""Backup scheduler (leader only): on each tick reads the policies and decides per app×environment (`schedule.plan`: wait,
run, skip a missed run) and triggers the same job as the "Backup agora" ("Back up now") action."""

from __future__ import annotations

import asyncio
import os
from functools import partial
from typing import Any

from fastapi import FastAPI

from hub.ops.leader import Leader
from hub.ops.model import OpsError, now_ms
from hub.ops.schedule import Cron, jitter_ms, local_label, normalize, plan, timezone_of

ACTOR = "plataforma (agendador)"


class Scheduler:
    def __init__(self, app: FastAPI, leader: Leader) -> None:
        self.app, self.leader = app, leader
        self.task: asyncio.Task[None] | None = None

    async def tick(self, now: int | None = None) -> list[str]:
        """One round; returns what was done (for the log)."""
        now = now_ms() if now is None else now
        st = self.app.state
        grace = int(os.getenv("HUB_BACKUP_MISSED_GRACE_S", "300") or 300) * 1000
        out: list[str] = []
        for p in st.ops.read()["policies"]:
            if st.gateway.registry.get(p["app"]) is None:
                continue
            for env in p["envs"]:
                await self._env(p, "staging" if env == "staging" else "producao", now, grace, out)
        return out

    async def _env(self, p: dict[str, Any], env: str, now: int, grace: int, out: list[str]) -> None:
        key = f"{p['app']}|{env}"
        prev: dict[str, Any] | None = self.app.state.ops.read()["schedules"].get(key)
        await self._refresh_running(key, prev)
        cron = await self._cron_of(p, key, prev)
        if cron is None:
            return
        tz = timezone_of(p.get("tz", ""))
        app_id, jit = p["app"], int(p.get("jitter_s", 20))
        nxt, decision, slot, missed = plan(
            prev,
            cron,
            tz,
            now,
            grace,
            p.get("missed", "executar"),
            partial(jitter_ms, app_id, env, max_s=jit),
        )
        if decision == "skip":
            when = local_label(slot, tz)
            self.app.state.admin.logs.add("WARN", p["app"], ACTOR, f"backup agendado de {when} perdido (hub fora do ar); política: pular", "ops")  # fmt: skip
            out.append(f"{key}: pulado {when}")
        elif decision == "run":
            nxt = await self._run(p["app"], env, key, prev, nxt, missed, local_label(slot, tz), now, out)
        if nxt != prev:
            await self._set(key, lambda s, n=nxt: (s.clear(), s.update(n)))

    async def _refresh_running(self, key: str, prev: dict[str, Any] | None) -> None:
        if not prev or prev.get("last_status") != "Em andamento":
            return
        jobs = self.app.state.ops_jobs
        job = jobs.get(prev.get("last_job", 0)) or await jobs.aget(prev.get("last_job", 0))
        if job and job["done"]:
            await self._set(
                key, lambda s, ok=job["ok"]: s.update(last_status="Concluído" if ok else "Falhou")
            )

    async def _cron_of(self, p: dict[str, Any], key: str, prev: dict[str, Any] | None) -> Cron | None:
        """The policy's cron (`None` = manual); an invalid schedule is recorded on the schedule as `error`."""
        try:
            exp = normalize(p["schedule"])
            return Cron(exp) if exp else None
        except OpsError as e:
            if (prev or {}).get("error") != e.message:
                await self._set(key, lambda s, m=e.message: s.update(error=m))
            return None

    async def _run(self, app: str, env: str, key: str, prev: dict[str, Any] | None, nxt: dict[str, Any],
                   missed: bool, when: str, now: int, out: list[str]) -> dict[str, Any]:  # fmt: skip
        kind = "Agendado (perdido, executado ao voltar)" if missed else "Agendado"
        try:
            job = await self.app.state.ops_start_backup(app, env, kind, ACTOR)
        except OpsError:
            out.append(f"{key}: adiado")
            return {**(prev or {}), "run_at": now + 15_000, "last_status": "Adiado (job em andamento)"}
        out.append(f"{key}: {kind} {when} (job {job['id']})")
        return nxt | {"last_status": "Em andamento", "last_job": job["id"], "last_at": now}

    async def _set(self, key: str, fn: Any) -> None:
        async with self.app.state.ops.locked(True) as d:
            fn(d["schedules"].setdefault(key, {}))

    def start(self) -> None:
        if os.getenv("HUB_BACKUP_SCHEDULER") == "0":
            return
        every = max(1, int(os.getenv("HUB_BACKUP_TICK_S", "15") or 15))

        async def loop() -> None:
            while True:
                await asyncio.sleep(every)
                if not await self.leader.checked():
                    continue
                self.leader.ran("scheduler")
                try:
                    for line in await self.tick():
                        print(f"hub: agendador de backups: {line}", flush=True)
                except Exception as e:
                    print(f"hub: agendador de backups falhou: {type(e).__name__}: {e}", flush=True)

        self.task = asyncio.create_task(loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
