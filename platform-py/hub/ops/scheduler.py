"""Agendador de backups (só no líder): a cada tick lê as políticas, decide por app×ambiente (`schedule.plan`: esperar,
rodar, pular execução perdida) e dispara o mesmo job do "Backup agora". Porta de `hub-ops/src/scheduler.rs`."""

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
        """Uma rodada; devolve o que foi feito (para o log)."""
        now = now_ms() if now is None else now
        st = self.app.state
        store, jobs = st.ops, st.ops_jobs
        grace = int(os.getenv("HUB_BACKUP_MISSED_GRACE_S", "300") or 300) * 1000
        out: list[str] = []
        for p in store.read()["policies"]:
            if st.gateway.registry.get(p["app"]) is None:
                continue
            for env in p["envs"]:
                env = "staging" if env == "staging" else "producao"
                key = f"{p['app']}|{env}"
                prev: dict[str, Any] | None = store.read()["schedules"].get(key)
                if prev and prev.get("last_status") == "Em andamento":
                    job = jobs.get(prev.get("last_job", 0)) or await jobs.aget(prev.get("last_job", 0))
                    if job and job["done"]:
                        await self._set(
                            key, lambda s, ok=job["ok"]: s.update(last_status="Concluído" if ok else "Falhou")
                        )
                try:
                    exp = normalize(p["schedule"])
                    cron = Cron(exp) if exp else None
                except OpsError as e:
                    if (prev or {}).get("error") != e.message:
                        await self._set(key, lambda s, m=e.message: s.update(error=m))
                    continue
                if cron is None:
                    continue
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
                    st.admin.logs.add("WARN", p["app"], ACTOR, f"backup agendado de {when} perdido (hub fora do ar) — política: pular", "ops")  # fmt: skip
                    out.append(f"{key}: pulado {when}")
                elif decision == "run":
                    kind = "Agendado (perdido, executado ao voltar)" if missed else "Agendado"
                    try:
                        job = await st.ops_start_backup(p["app"], env, kind, ACTOR)
                        nxt |= {"last_status": "Em andamento", "last_job": job["id"], "last_at": now}
                        out.append(f"{key}: {kind} {local_label(slot, tz)} (job {job['id']})")
                    except OpsError:
                        nxt = {
                            **(prev or {}),
                            "run_at": now + 15_000,
                            "last_status": "Adiado (job em andamento)",
                        }
                        out.append(f"{key}: adiado")
                if nxt != prev:
                    await self._set(key, lambda s, n=nxt: (s.clear(), s.update(n)))
        return out

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
