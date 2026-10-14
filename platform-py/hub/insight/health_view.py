"""Rows of the Health page: SLOs, services and the Observability section (charts and slow requests)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request

from hub.admin.config import env_label
from hub.admin.routes import dumps, relative
from hub.insight.fmt import pc2
from hub.insight.insightctx import InsightCtx
from hub.insight.metrics import Metrics, slo_status
from hub.insight.view import chart, ms_text

_STATUS_CSS = {
    "Disponível": "status-pill status-active",
    "Manutenção": "status-pill status-maintenance",
    "Indisponível": "status-pill status-maintenance",
}


def slo_rows(
    slos: list[dict[str, Any]], metrics: Metrics, short: Callable[[str], str]
) -> list[dict[str, Any]]:
    return [_slo_row(s, slo_status(metrics, s), short) for s in slos]


def _slo_row(s: dict[str, Any], x: dict[str, Any], short: Callable[[str], str]) -> dict[str, Any]:
    b = max(-1.0, min(1.0, x["budget_remaining"]))
    color = "var(--color-success)" if b > 0.25 else "var(--color-warning)" if b > 0 else "var(--color-danger)"
    return {
        "app": short(s["app"]),
        "env": env_label(s["env"]),
        "target": f"{s['availability']:g}% · {s['latency_target']:g}% ≤ {s['latency_ms']} ms · {s['window_days']} dias".replace(
            ".", ","
        ),
        "sli": f"{pc2(x['availability'])} ({x['requests']} req.)",
        "latency": pc2(x["latency"]) if x["latency"] is not None else "—",
        "budget": pc2(max(x["budget_remaining"], -9.99)),
        "budget_style": f"width:{max(b, 0.0) * 100:.0f}%;background:{color}",
        "burn": f"{x['burn_1h']:.1f}× (1 h) · {x['burn_5m']:.1f}× (5 min)".replace(".", ","),
        "css": "status-pill status-active" if x["met"] else "status-pill status-maintenance",
        "state": "Dentro da meta" if x["met"] else "Fora da meta",
        "item": json.dumps(s, ensure_ascii=False, separators=(",", ":")),
    }


def health_rows(health: list[dict[str, Any]], now: int) -> list[dict[str, Any]]:
    return [
        {
            "css": _STATUS_CSS.get(s["status"], "status-pill status-draft"),
            "latency": f"{s['latency_ms']} ms" if s.get("latency_ms") is not None else "—",
            "checked": relative(s["checked_at"], now) if s.get("checked_at") else "—",
            "item": dumps({"name": s["name"], "kind": s["kind"], "status": s["status"], "app": s.get("app")}),
            "menu": f"health:{s['name']}",
            "detail": s.get("detail") or "",
            "name": s["name"],
            "kind": s["kind"],
            "status": s["status"],
        }
        for s in health
    ]


def obs_ctx(c: InsightCtx, request: Request, app_q: str, env: str, min: str) -> dict[str, Any]:
    """Health charts and slow requests (filters by app, environment and window)."""
    allow = getattr(request.state, "apps", None)
    visible = lambda a: allow is None or a in allow  # noqa: E731
    apps_v = [a for a in c.refs() if visible(a["id"])]
    sel = app_q if any(a["id"] == app_q for a in apps_v) else (apps_v[0]["id"] if apps_v else "")
    env = "staging" if env == "staging" else "producao"
    minutes = int(min) if min in ("15", "30", "60") else 30
    chip = lambda on: "filter-chip active" if on else "filter-chip"  # noqa: E731
    url = lambda a, e, m: f"/admin/health?app={a}&env={e}&min={m}#obs"  # noqa: E731
    tel = c.tel
    otlp = (
        f"OTLP ligado: exportando traces e métricas para {tel.otlp}."
        if tel.otlp
        else "OTLP desligado (defina OTEL_EXPORTER_OTLP_ENDPOINT para enviar a um coletor)."
    )
    return {
        "app_chips": [(url(a["id"], env, minutes), a["short"], chip(a["id"] == sel)) for a in apps_v],
        "env_chips": [(url(sel, e, minutes), env_label(e), chip(e == env)) for e in ("producao", "staging")],
        "win_chips": [(url(sel, env, mm), f"{mm} min", chip(mm == minutes)) for mm in (15, 30, 60)],
        "charts": _charts(tel.metrics.points(sel, env, minutes)),
        "traces": _traces(c, sel),
        "otlp": otlp,
    }


def _charts(pts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    col = lambda k, f=lambda v: v: [None if p[k] is None else f(p[k]) for p in pts]  # noqa: E731
    return [
        chart("Requisições por segundo", "req/s", [("req/s", "var(--color-accent)", col("rps"))]),
        chart(
            "Taxa de erro (5xx)",
            "%",
            [("% erros", "var(--color-danger)", col("error_rate", lambda v: v * 100))],
        ),
        chart(
            "Latência (gateway → app)",
            "ms",
            [
                ("p50", "var(--color-success)", col("p50")),
                ("p95", "var(--color-warning)", col("p95")),
                ("p99", "var(--color-danger)", col("p99")),
            ],
        ),
        chart("CPU do container", "vCPU", [("vCPU", "var(--color-violet)", col("cpu"))]),
        chart(
            "Memória do container",
            "MiB",
            [("MiB", "var(--color-info)", col("mem", lambda b: b / 1048576))],
        ),
    ]


def _traces(c: InsightCtx, sel: str) -> list[dict[str, Any]]:
    nowus = time.time_ns() // 1000
    return [
        {
            "name": x["name"],
            "app": f"{c.short(x['app'])} · {env_label(x['env'])}",
            "dur": ms_text(x["duration_us"]),
            "when": relative(x["start_us"] // 1000, nowus // 1000),
            "spans": x["spans"],
            "css": "status-pill status-maintenance" if x["error"] else "status-pill status-active",
            "status": x["status"],
        }
        for x in c.tel.traces.slow(sel, 15)
    ]
