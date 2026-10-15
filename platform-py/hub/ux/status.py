"""Status page data: current situation, 30-day bars, SLO and incidents (the hub's own metrics)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from hub.admin.config import env_label
from hub.admin.routes import relative
from hub.gateway.registry import Registry
from hub.insight.fmt import num, pct
from hub.insight.metrics import Metrics, daily_uptime, now_s, slo_status
from hub.ux.catalog import state_label

_GMT3 = timezone(timedelta(hours=-3))
_PILL = {"major": "status-pill status-maintenance", "minor": "status-pill status-review"}


@dataclass
class StatusApp:
    name: str
    env: str
    state: str
    css: str
    uptime: str
    bars: list[tuple[str, str]]
    slo: str | None = None


@dataclass
class StatusData:
    apps: list[StatusApp] = field(default_factory=list)
    incidents: list[dict[str, Any]] = field(default_factory=list)
    overall: str = "Todos os sistemas operacionais"


def _label(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, _GMT3).strftime("%d/%m")


def current_state(m: Metrics | None, app: str, env: str, open_: list[dict[str, Any]]) -> str:
    """Open incident > last probe > traffic: `down`, `degraded`, `unstable` or `ok`."""
    mine = [i for i in open_ if i["app"] == app and i["env"] == env]
    if any(i["severity"] == "major" for i in mine):
        return "down"
    if mine:
        return "degraded"
    if m:
        now = now_s()
        w = m.window(app, env, max(now - 600, 0), now + 1)
        if w.probes and w.probes_ok < w.probes:
            return "unstable"
    return "ok"


def app_states(
    reg: Registry,
    visible: set[str] | None,
    m: Metrics | None = None,
    incidents: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str, str]]:
    """(id, name, status key) of the apps visible in production."""
    open_ = [i for i in incidents or [] if i["resolved"] is None]
    return [
        (e.id, e.name, current_state(m, e.id, "producao", open_))
        for e in reg.entries()
        if visible is None or e.id in visible
    ]


def _uptime_bars(days: list[tuple[int, float | None]]) -> list[tuple[str, str]]:
    bars: list[tuple[str, str]] = []
    for t, v in days:
        date = _label(t * 1000)
        cls = "none" if v is None else "ok" if v >= 0.999 else "warn" if v >= 0.98 else "bad"
        bars.append((f"uptime-bar uptime-{cls}", f"{date}: sem dados" if v is None else f"{date}: {pct(v)}"))
    return bars


def _slo_line(m: Metrics | None, slos: list[dict[str, Any]], app_id: str) -> str | None:
    slo = next((s for s in slos if s["app"] == app_id and s["env"] == "producao"), None)
    if not (slo and m):
        return None
    x = slo_status(m, slo)
    return (
        f"SLO {num(slo['availability'], 2)}% · atual {pct(x['availability'])} · "
        f"orçamento restante {pct(max(x['budget_remaining'], -9.99))}"
    )


def _status_app(
    m: Metrics | None, slos: list[dict[str, Any]], app_id: str, name: str, key: str, now: float
) -> StatusApp:
    label, css = state_label(key)
    days = daily_uptime(m, app_id, "producao", 30, int(now)) if m else []
    known = [v for _, v in days if v is not None]
    uptime = f"{pct(sum(known) / len(known))} em 30 dias" if known else "sem dados"
    bars = _uptime_bars(days)
    return StatusApp(
        name, "Produção", label, css, uptime, bars or _empty_bars(now), _slo_line(m, slos, app_id)
    )


def _incident_row(i: dict[str, Any], names: dict[str, str], ms: int) -> dict[str, Any]:
    done = i["resolved"] is not None
    return {
        "id": i["id"],
        "title": i["title"],
        "meta": f"{names.get(i['app'], 'Plataforma')} · {env_label(i['env'])} · "
        f"{'automático' if i['kind'] == 'auto' else 'manual'}"
        + (
            f" · resolvido {relative(i['resolved'], ms)}"
            if done
            else f" · desde {relative(i['started'], ms)}"
        ),
        "css": "status-pill status-draft" if done else _PILL.get(i["severity"], _PILL["minor"]),
        "state": "Resolvido" if done else "Indisponível" if i["severity"] == "major" else "Em andamento",
        "notes": [(f"{n['author']} · {relative(n['at'], ms)}", n["text"]) for n in reversed(i["notes"][-5:])],
        "open": not done,
    }


def status_data(
    reg: Registry,
    visible: set[str] | None,
    now: float,
    m: Metrics | None = None,
    admin_data: dict[str, Any] | None = None,
) -> StatusData:
    data, admin_data = StatusData(), admin_data or {}
    shown = lambda app: visible is None or app in visible  # noqa: E731
    incidents = [i for i in admin_data.get("incidents", []) if shown(i["app"])]
    open_ = [i for i in incidents if i["resolved"] is None]
    slos = admin_data.get("slos", [])
    names = {e.id: e.display.get("short") or e.name for e in reg.entries()}
    data.apps = [_status_app(m, slos, a, n, k, now) for a, n, k in app_states(reg, visible, m, incidents)]
    ms = int(now * 1000)
    data.incidents = [_incident_row(i, names, ms) for i in incidents[:30]]
    if any(i["severity"] == "major" for i in open_):
        data.overall = "Há aplicações indisponíveis"
    elif open_:
        data.overall = "Algumas aplicações com problemas"
    return data


def _empty_bars(now: float, days: int = 30) -> list[tuple[str, str]]:
    today = int(now) - int(now) % 86_400
    return [
        (
            "uptime-bar uptime-none",
            f"{datetime.fromtimestamp(today - i * 86_400, UTC).astimezone(_GMT3):%d/%m}: sem dados",
        )
        for i in range(days - 1, -1, -1)
    ]
