"""In-memory Operations jobs (`GET /api/ops/jobs/{id}`) and the apps' durable tasks (`/{id}/_hub/jobs`)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Callable
from typing import Any

import asyncpg
import httpx

from hub.ops.model import OpsError
from hub.state import StateDb

KEEP, MAX_TASKS_PER_APP = 200, 100
TOKEN_HEADER = "x-hub-internal-token"  # noqa: S105


class Jobs:
    """Operations jobs. In memory in the replica that runs them; with the state database, each change also goes to the
    `jobs` table (ids from a database sequence): any replica answers `GET /api/ops/jobs/{id}`."""

    def __init__(self, db: StateDb | None = None) -> None:
        self.items: dict[int, dict[str, Any]] = {}
        self.last = 0
        self.busy: set[str] = set()
        self.db = db
        self.sent: dict[int, str] = {}
        self.task: asyncio.Task[None] | None = None

    async def create(self, kind: str, title: str, steps: list[dict[str, Any]] | None = None,
                     app: str | None = None, env: str | None = None) -> dict[str, Any]:  # fmt: skip
        if self.db:
            self.last = await self.db.job_next_id()
        else:
            self.last += 1
        job: dict[str, Any] = {"id": self.last, "kind": kind, "title": title, "steps": steps or [], "done": False,
                               "ok": False, "message": "", "progress": 0, "result": None}  # fmt: skip
        if app:
            job["app"] = app
        if env:
            job["env"] = env
        self.items[self.last] = job
        while len(self.items) > KEEP:
            del self.items[next(iter(self.items))]
        await self.flush()
        return job

    def get(self, i: int) -> dict[str, Any] | None:
        return self.items.get(i)

    async def aget(self, i: int) -> dict[str, Any] | None:
        """This replica's job or, if it belongs to another replica, the one written to the database."""
        if (j := self.items.get(i)) is not None or self.db is None:
            return j
        v = await self.db.job_get(i)
        return v if isinstance(v, dict) else None

    def update(self, i: int, fn: Callable[[dict[str, Any]], None]) -> None:
        if j := self.items.get(i):
            fn(j)

    async def flush(self) -> None:
        """Writes to the database the jobs that changed since the last write."""
        if not self.db:
            return
        for i, j in list(self.items.items()):
            raw = json.dumps(j, sort_keys=True, default=str)
            if self.sent.get(i) != raw:
                await self.db.job_put(i, j, bool(j["done"]))
                self.sent[i] = raw

    def start(self) -> None:
        if self.db and self.task is None:
            self.task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(0.5)
            with contextlib.suppress(asyncpg.PostgresError, OSError):
                await self.flush()

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        with contextlib.suppress(asyncpg.PostgresError, OSError):
            await self.flush()

    async def claim(self, app: str) -> None:
        """One backup/restore job per app (also across replicas: checks the open jobs in the database)."""
        if app in self.busy:
            raise OpsError("Já existe um job de backup em andamento para esta aplicação.", 409)
        if self.db:
            for v in await self.db.job_list(200):
                if (isinstance(v, dict) and not v.get("done") and v.get("app") == app
                        and v.get("kind") in ("backup", "restore") and v["id"] not in self.items):  # fmt: skip
                    raise OpsError("Já existe um job de backup em andamento para esta aplicação.", 409)
        self.busy.add(app)

    def running(self) -> int:
        return sum(not j["done"] for j in self.items.values())

    async def running_all(self) -> int:
        """Unfinished jobs from all replicas."""
        if not self.db:
            return self.running()
        return sum(isinstance(v, dict) and not v.get("done") for v in await self.db.job_list(200))

    async def interrupt_dead(self) -> int:
        """Jobs whose owning replica has shown no sign of life for 30 s become "interrupted"."""
        if not self.db:
            return 0
        alive = {r[0] for r in await self.db.replicas(30_000)}
        n = 0
        for v in await self.db.job_list(200):
            if not isinstance(v, dict) or v.get("done") or v["id"] in self.items:
                continue
            if (await self.db.job_owner(v["id"])) in alive:
                continue
            v |= {
                "done": True,
                "ok": False,
                "message": "Interrompido: a réplica do hub que executava este job caiu.",
            }
            await self.db.job_put(v["id"], v, True)
            n += 1
        return n


def label(j: dict[str, Any]) -> str:
    s = j["status"]
    if s == "queued":
        return "Na fila (nova tentativa)" if j["attempts"] > 0 else "Na fila"
    if s == "running":
        return "Cancelando" if j.get("cancel_requested") else "Em execução"
    return {
        "done": "Concluída",
        "failed": "Falhou",
        "dead": "Tentativas esgotadas",
        "cancelled": "Cancelada",
    }.get(s, "Desconhecido")


def percent(j: dict[str, Any]) -> int:
    if j["status"] == "done":
        return 100
    t = j.get("progress_total", 0)
    return int(max(0, min(100, j.get("progress_done", 0) * 100 // t))) if t else 0


def can_retry(j: dict[str, Any]) -> bool:
    return j["status"] in ("failed", "dead", "cancelled")


def can_cancel(j: dict[str, Any]) -> bool:
    return j["status"] in ("queued", "running") and not j.get("cancel_requested")


def saga_summary(cp: Any) -> dict[str, Any] | None:
    s = cp.get("saga") if isinstance(cp, dict) else None
    if not isinstance(s, dict) or not isinstance(s.get("steps"), list):
        return None
    return {"state": s.get("state"), "steps": [{"name": p.get("name"), "state": p.get("state")} for p in s["steps"][:50]],
            "reason": s.get("reason")}  # fmt: skip


async def list_all(
    client: httpx.AsyncClient, targets: list[tuple[str, str, str, str | None]], name_of: Callable[[str], str]
) -> dict[str, Any]:
    """`targets` = (app, env, upstream, token). Most recent tasks first; silent apps go into `errors`."""

    async def one(app: str, env: str, up: str, token: str | None) -> tuple[str, str, Any]:
        if not token:
            return app, env, "sem token interno"
        try:
            r = await client.get(f"{up.rstrip('/')}/{app}/_hub/jobs", params={"limit": MAX_TASKS_PER_APP},
                                 headers={TOKEN_HEADER: token}, timeout=5)  # fmt: skip
            if r.status_code != 200:
                return app, env, f"respondeu {r.status_code}"
            return app, env, r.json()["jobs"][:MAX_TASKS_PER_APP]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            return (
                app,
                env,
                f"sem resposta ({e})" if isinstance(e, httpx.HTTPError) else f"resposta inválida ({e})",
            )

    out: dict[str, Any] = {"tasks": [], "errors": []}
    for app, env, res in await asyncio.gather(*(one(*t) for t in targets)):
        if isinstance(res, str):
            out["errors"].append([f"{app} (staging)" if env == "staging" else app, res])
            continue
        for j in res:
            j["saga"] = saga_summary(j.pop("checkpoint", None))
            out["tasks"].append({"app": app, "app_name": name_of(app), "env": env, **j})
    out["tasks"].sort(key=lambda t: -int(t.get("created_at", 0)))
    return out
