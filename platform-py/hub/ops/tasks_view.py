"""Operations › Tasks: the rows of the table and what the side panel shows (details and the app's log while the task
ran). Pure functions: the route passes the tasks the apps answered and the log lines the hub keeps."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from hub.admin.store import log_stamp
from hub.ops import jobs as jb
from hub.ops.opsctx import tone

SAGA = {"running": "em andamento", "compensating": "compensando", "completed": "concluída", "compensated": "compensada",
        "failed": "falhou (intervenção manual)"}  # fmt: skip
SAGA_STEP = {"pending": "pendente", "done": "feito", "attempted": "falhou", "compensating": "desfazendo",
             "compensated": "desfeito", "failed": "erro ao desfazer"}  # fmt: skip
LOG_MAX = 200  # lines in the panel (the newest)
_MARGIN_MS = 5_000  # the app may log just before or after the task's own timestamps


def _ms(t: dict[str, Any], key: str) -> int:
    try:
        return max(int(t.get(key) or 0), 0)
    except (TypeError, ValueError):
        return 0


def window(t: dict[str, Any], now: int) -> tuple[int, int]:
    """(start, end) of the task: from its creation to its end (or now, while it runs)."""
    start = _ms(t, "created_at")
    end = _ms(t, "finished_at") or (now if t.get("status") in ("queued", "running") else 0)
    end = end or _ms(t, "updated_at") or start + int(float(t.get("elapsed_s") or 0) * 1000)
    return start - _MARGIN_MS, max(end, start) + _MARGIN_MS


def task_log(lines: Iterable[dict[str, Any]], t: dict[str, Any], now: int) -> list[dict[str, str]]:
    """The app's lines inside the task's window, plus any line of the app that names the task (e.g. a later error)."""
    start, end = window(t, now)
    picked = [x for x in lines if x.get("app") == t["app"]
              and (start <= int(x.get("at", 0)) <= end or t["id"] in str(x.get("message", "")))]  # fmt: skip
    return [{"time": str(x.get("time", "")), "level": str(x.get("level", "INFO")), "message": str(x.get("message", ""))}
            for x in picked[-LOG_MAX:]]  # fmt: skip


def _progress(t: dict[str, Any]) -> str:
    total, done = t.get("progress_total", 0), t.get("progress_done", 0)
    if total > 0:
        return f"{jb.percent(t)}% · {done} de {total}"
    return f"{done} · {t.get('progress_text', '')}" if done > 0 else f"{jb.percent(t)}%"


def _result(t: dict[str, Any]) -> str:
    res = t.get("result")
    return "" if res in (None, "", {}, []) else json.dumps(res, ensure_ascii=False, default=str)[:2000]


def duration_label(seconds: float) -> str:
    """`57s`, `20m3s`, `1h2m5s` (rounded to the second)."""
    total = round(max(seconds, 0))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h{m}m{s}s"
    return f"{m}m{s}s" if m else f"{s}s"


def task_row(t: dict[str, Any], n: int, log: list[dict[str, str]]) -> dict[str, Any]:
    """`n` = the row's number in the listing (1 = the newest), shown instead of the job id."""
    lbl = jb.label(t)
    sg = t.get("saga")
    key = f"{t['app']}|{t['id']}"
    finished = _ms(t, "finished_at")
    return {
        "n": n, "id": t["id"], "kind": t["kind"], "app_id": t["app"],
        "app": f"{t['app_name']} (staging)" if t["env"] == "staging" else t["app_name"],
        "status_key": t["status"], "status": lbl, "status_class": tone(lbl), "user": t.get("user", ""),
        "created": log_stamp(_ms(t, "created_at")), "created_ms": str(t.get("created_at", 0)),
        "finished": log_stamp(finished) if finished else "",
        "pct": f"width:{jb.percent(t)}%", "progress": _progress(t), "attempts": f"{t['attempts']} de {t['max_attempts']}",
        "duration": duration_label(float(t.get("elapsed_s", 0))), "error": (t.get("error") or "")[:160],
        "error_full": t.get("error") or "", "result": _result(t), "log": log,
        "saga": (SAGA.get(sg.get("state"), "desconhecido"), [(p["name"] or "", SAGA_STEP.get(p["state"], "?")) for p in sg["steps"]]) if sg else None,
        "retry": f"{key}|retry|{t['env']}" if jb.can_retry(t) else None,
        "cancel": f"{key}|cancel|{t['env']}" if jb.can_cancel(t) else None,
    }  # fmt: skip
