"""Native `/api/v1` operations (answered by the hub itself, not by an internal route): apps, status and
observability."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import Request

from hub.api.ops import Op
from hub.api.v1_base import Args, P, Problem


def visible_app(p: P, app: str) -> bool:
    return p.can_app(app) or (p.has("editar_configuracoes") and p.allows_app(app))


def app_json(request: Request, app_id: str) -> dict[str, Any] | None:
    reg = request.app.state.gateway.registry
    e = reg.get(app_id)
    if e is None or e.manifest is None:
        return None
    m, cfg = e.manifest, request.app.state.apicfg
    extra = m.model_extra or {}
    api, mcp = m.api or {}, m.mcp or {}
    api_on, mcp_on = api.get("enabled", True), mcp.get("enabled", True)
    jobs = [j.get("name") if isinstance(j, dict) else j for j in extra.get("jobs", [])]
    return {
        "id": m.id, "name": m.name or m.id, "version": m.version or "", "mode": extra.get("mode", "fragment"),
        "stack": (m.runtime or {}).get("stack"), "routes": extra.get("routes", []),
        "status": "ok", "url": f"/apps/{m.id}",
        "api": {"enabled": api_on, "base": f"/apps/{m.id}{api.get('base', '/api/v1')}",
                "openapi": f"/apps/{m.id}{api.get('openapi', '/api/openapi.json')}"},
        "mcp": {"enabled": mcp_on and api_on,
                "exposed_by_hub": cfg.mcp.enabled and cfg.mcp.expose_app_tools and mcp_on and api_on,
                "tool_prefix": f"{m.id}."},
        "jobs": jobs,
    }  # fmt: skip


async def _states(request: Request, vis: Iterable[str] | None) -> list[tuple[str, str, str]]:
    from hub.ux import status

    ins = request.app.state.insight
    allowed = None if vis is None else set(vis)
    return status.app_states(
        request.app.state.gateway.registry, allowed, ins.tel.metrics, await ins.ops.incidents()
    )


def _visible(request: Request, p: P) -> list[str]:
    return [e.id for e in request.app.state.gateway.registry.entries() if visible_app(p, e.id)]


async def _apps(request: Request, p: P) -> list[dict[str, Any]]:
    visible = _visible(request, p)
    states = {i: s for i, _, s in await _states(request, visible)}
    return [a | {"status": states.get(a["id"], "ok")} for i in visible if (a := app_json(request, i))]


async def _app(request: Request, op: Op, p: P, args: Args) -> dict[str, Any]:
    from hub.insight.metrics import now_s, slo_status

    app_id = args.path.get("app", "")
    if not visible_app(p, app_id) or not (base := app_json(request, app_id)):
        raise Problem(404, "not-found", "App não encontrado ou fora do seu acesso.")
    states = {i: s for i, _, s in await _states(request, [app_id])}
    base["status"] = states.get(app_id, "ok")
    if op.native == "app":
        return base
    ins = request.app.state.insight
    cfgd = await ins.ops.s.cfg.read()
    inc = [i for i in cfgd.get("incidents", []) if i["app"] == app_id and i["resolved"] is None]
    m = ins.tel.metrics
    slos = [slo_status(m, s) | {"app": s["app"], "env": s["env"]} for s in cfgd.get("slos", [])
            if s["app"] == app_id]  # fmt: skip
    now = now_s()
    w = m.window(app_id, "producao", max(now - 3600, 0), now + 1)
    return {"app": app_id, "status": base["status"], "version": base["version"], "open_incidents": inc,
            "slos": slos, "last_hour": _win(w, max(now - 3600, 0), now + 1)}  # fmt: skip


async def _status(request: Request, p: P) -> dict[str, Any]:
    states = await _states(request, _visible(request, p))
    incidents = await request.app.state.insight.ops.incidents()
    inc = [i for i in incidents if i["resolved"] is None and visible_app(p, i["app"])]
    worst = ("down" if any(s[2] == "down" for s in states)
             else "degraded" if any(s[2] != "ok" for s in states) else "ok")  # fmt: skip
    return {"overall": worst, "apps": [{"id": i, "name": n, "status": s} for i, n, s in states],
            "open_incidents": inc}  # fmt: skip


def _observability(request: Request, p: P, args: Args) -> dict[str, Any]:
    from hub.insight.metrics import now_s

    minutes = int(args.q("minutes") or 15)
    minutes = minutes if minutes in (15, 30, 60) else 15
    only, now, out = args.q("app"), now_s(), []
    metrics = request.app.state.insight.tel.metrics
    for e in request.app.state.gateway.registry.entries():
        if (only and only != e.id) or (p.apps is not None and e.id not in p.apps) or not p.allows_app(e.id):
            continue
        for env in ("producao", "staging"):
            w = metrics.window(e.id, env, max(now - minutes * 60, 0), now + 1)
            if env == "staging" and w.requests == 0 and w.probes == 0:
                continue
            out.append({"app": e.id, "env": env, "minutes": minutes, "requests": w.requests,
                        "errors": w.errors, "availability": w.availability, "p95_ms": w.p95,
                        "probes": w.probes, "probes_ok": w.probes_ok})  # fmt: skip
    return {"minutes": minutes, "items": out}


async def native(request: Request, op: Op, p: P, args: Args) -> Any:
    match op.native:
        case "apps":
            return await _apps(request, p)
        case "app" | "app_status":
            return await _app(request, op, p, args)
        case "status":
            return await _status(request, p)
        case _:  # observability
            return _observability(request, p, args)


def _win(w: Any, frm: int, to: int) -> dict[str, Any]:
    return {"availability": w.availability, "errors": w.errors, "requests": w.requests, "probes": w.probes,
            "probes_ok": w.probes_ok, "p95": w.p95, "from": frm, "to": to}  # fmt: skip
