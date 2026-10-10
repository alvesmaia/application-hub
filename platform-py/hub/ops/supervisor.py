"""Supervisão dos containers dos apps (só no líder; port enxuto de `hub-ops::supervisor`): religa o que caiu ou ficou
`unhealthy` (recuo exponencial, laço de quedas vira alerta), alimenta os gráficos de CPU/memória da Observabilidade e
leva a saída dos containers para o console de logs (origem "container"). Parada pelo operador não é religada."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Any

from fastapi import FastAPI

from hub.ops import runtime as rt
from hub.ops.leader import Leader
from hub.ops.model import IMPLANTADO, STAGING, OpsError

ACTOR = "plataforma (supervisor)"
MAX_RESTARTS, WINDOW_S = 5, 600


class Supervisor:
    def __init__(self, app: FastAPI, leader: Leader) -> None:
        self.app, self.leader = app, leader
        self.state: dict[str, dict[str, Any]] = {}  # app|env -> restarts, last, next, loop
        self.seen: dict[str, int] = {}  # container -> epoch s da última linha de log levada ao console
        self.task: asyncio.Task[None] | None = None

    def status(self, app: str, env: str = "producao") -> dict[str, Any] | None:
        return self.state.get(f"{app}|{env}")

    def stopped(self) -> set[str]:
        return set(self.app.state.ops.read().get("stopped", []))

    async def mark_stopped(self, app: str, env: str, on: bool) -> None:
        async with self.app.state.ops.locked(True) as d:
            s = set(d.setdefault("stopped", []))
            (s.add if on else s.discard)(f"{app}|{env}")
            d["stopped"] = sorted(s)

    async def tick(self) -> list[str]:
        st = self.app.state
        eng, reg = st.ops_engine, st.gateway.registry
        info = await eng.probe(0)
        if not info["reachable"]:
            return []
        live = {x["name"]: x for x in info["live"]}
        for e in (
            reg.entries()
        ):  # staging do gateway = container `<app>__staging` em execução (vale em toda réplica)
            n = rt.container_name(e.id, "staging")
            if n in live and live[n]["state"] == "running":
                reg.staging[e.id] = st.ops_rollout.url_of(n)
            else:
                reg.staging.pop(e.id, None)
        data, stopped = st.ops.read(), self.stopped()
        out: list[str] = []
        for e in reg.entries():
            for env in ("producao", "staging"):
                name = rt.container_name(e.id, env)
                lv = live.get(name)
                if lv is None:
                    heal = env == "staging" and self.leader.leading() and f"{e.id}|{env}" not in stopped
                    if heal and (r := await self.heal_staging(e, data)):
                        out.append(r)
                    continue
                st.insight.tel.metrics.set_resources(e.id, env, lv["cpu_used"], lv["mem_used"])
                with contextlib.suppress(Exception):
                    await self.ship_logs(name, e.id)
                key = f"{e.id}|{env}"
                if key in stopped or key in data["maintenance"] or not self.leader.leading():
                    continue
                if lv["state"] != "running" or "(unhealthy)" in lv["status"]:
                    if r := await self.revive(e.id, env, name, lv["state"]):
                        out.append(r)
                elif (s := self.state.get(key)) and time.time() - s["last"] > WINDOW_S:
                    self.state.pop(key, None)
        self.leader.ran("supervisor")
        return out

    async def heal_staging(self, e: Any, data: dict[str, Any]) -> str | None:
        """Recria o `<app>__staging` que sumiu a partir da última implantação de staging (com recuo)."""
        dep = next((x for x in data["deployments"] if x["app"] == e.id and x["env"] == STAGING
                    and x["status"] == IMPLANTADO and x.get("image")), None)  # fmt: skip
        key, now = f"{e.id}|staging", time.time()
        s = self.state.setdefault(key, {"restarts": 0, "last": 0.0, "next": 0.0, "loop": False, "times": []})
        if dep is None or key in data["maintenance"] or now < s["next"]:
            return None
        s["next"] = now + 300
        st = self.app.state
        try:
            await st.ops_rollout.apply(
                e.id, "staging", rt.resources_of(e, data["limits"]), dep["version"], dep["image"]
            )
        except OpsError as ex:
            return f"{e.id} staging: {ex.message}"
        msg = f"container {rt.container_name(e.id, 'staging')} recriado pela supervisão (versão {dep['version']})"
        st.admin.logs.add("WARN", e.id, ACTOR, msg, "ops")
        await st.admin.audit(ACTOR, "Staging recriado", msg, icon="box", scope=e.id)
        return msg

    async def revive(self, app: str, env: str, name: str, why: str) -> str | None:
        st, now = self.app.state, time.time()
        s = self.state.setdefault(f"{app}|{env}", {"restarts": 0, "last": 0.0, "next": 0.0, "loop": False,
                                                    "times": []})  # fmt: skip
        if now < s["next"]:
            return None
        s["times"] = [t for t in s["times"] if now - t < WINDOW_S]
        if len(s["times"]) >= MAX_RESTARTS:
            if not s["loop"]:
                s["loop"] = True
                msg = f"{name} caiu {MAX_RESTARTS} vezes em {WINDOW_S // 60} min: a supervisão parou de religar."
                st.admin.logs.add("ERROR", app, ACTOR, msg, "ops")
                await st.platform_alert("error", f"{name} em laço de quedas", msg, "/admin/runtime")
            s["next"] = now + 60
            return None
        try:
            await st.ops_engine.action(name, "restart" if why == "running" else "start")
        except OpsError as e:
            s["next"] = now + 30
            return f"{name}: {e.message}"
        s["restarts"] += 1
        s["times"].append(now)
        s["last"], s["next"] = now, now + min(10 * 2 ** (len(s["times"]) - 1), 300)
        msg = f"container {name} religado pela supervisão ({why})"
        st.admin.logs.add("WARN", app, ACTOR, msg, "ops")
        await st.admin.audit(ACTOR, "Container religado", f"{name} · {why}", icon="box", scope=app)
        return msg

    async def ship_logs(self, name: str, app: str) -> None:
        since = self.seen.setdefault(name, int(time.time()))
        lines = await self.app.state.ops_engine.tail_since(name, since)
        self.seen[name] = int(time.time())
        for ts, text in lines[-50:]:
            kind = "stderr" if "Traceback" in text else "stdout"
            self.app.state.admin.logs.add(rt.level_of(kind, text), app, "", text[:500], "container", ts)

    def start(self) -> None:
        self.task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        every = float(os.getenv("HUB_SUPERVISOR_S", "15") or 15)
        await asyncio.sleep(3)
        while True:
            with contextlib.suppress(Exception):
                await self.tick()
            await asyncio.sleep(every)

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
