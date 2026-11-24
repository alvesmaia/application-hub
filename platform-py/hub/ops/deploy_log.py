"""Log of a deployment kept in its record (`ops.json`): each action (import, approval, promotion, rollback) appends
its pipeline steps, build lines and result as "[date time]: text" lines, so admin/deploys can show the whole history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from hub.ops import model as m
from hub.ops.opsctx import OpsCtx

MAX_LINES = 600
_GMT3 = timezone(timedelta(hours=-3))  # same fixed GMT-3 as the other screens
_STATE = {"ok": "ok", "failed": "falhou", "running": "em andamento", "pending": "pendente", "skipped": "pulado",
          "warn": "aviso"}  # fmt: skip


def lines_of(job: dict[str, Any], title: str, now_ms: int) -> list[str]:
    """The job as "[date time]: text" lines: the start of the action, one line per pipeline step, the build output
    and the final message (steps carry no time of their own: the action's time is used)."""
    at = datetime.fromtimestamp(now_ms / 1000, UTC).astimezone(_GMT3).strftime("%d/%m/%Y %H:%M:%S")
    out = [f"Início: {title}"]
    for s in job.get("steps") or []:
        state = _STATE.get(str(s.get("state")), str(s.get("state", "")))
        out.append(f"{s.get('name', '')}: {state}" + (f" · {s['detail']}" if s.get("detail") else ""))
    out += [f"build · {ln}" for ln in job.get("build_log") or []]
    if job.get("message"):
        out.append(f"Resultado: {job['message']}")
    return [f"[{at}]: {ln}"[:420] for ln in out]


async def keep(ops: OpsCtx, dep_id: int, job_id: int, title: str) -> None:
    """Appends the job's log to the deployment record (oldest lines leave past `MAX_LINES`)."""
    job = ops.jobs.get(job_id)
    if job is None:
        return
    lines = lines_of(job, title, m.now_ms())
    async with ops.store.locked(True) as d:
        dep = m._find(d, dep_id)
        dep["log"] = [*(dep.get("log") or []), *lines][-MAX_LINES:]
        if title == "Importação":  # the validation pipeline of the package, shown like the Import tab
            dep["pipeline"] = [
                {k: str(st.get(k) or "") for k in ("name", "state", "detail")}
                for st in job.get("steps") or []
            ]
