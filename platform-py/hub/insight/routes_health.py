"""Health page (services, SLOs, observability), its APIs (services, probes, SLOs, incidents) and feature flags."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from hub.admin.config import AdminError
from hub.admin.routes import dumps
from hub.insight.health_view import health_rows, obs_ctx, slo_rows
from hub.insight.insightctx import InsightCtx, ctx
from hub.ops import updates

pages = APIRouter()
router = APIRouter()  # under /api/admin (JSON only)


# ---- Health, SLOs and incidents ----------------------------------------------------------------------
@pages.get("/admin/health", response_class=HTMLResponse)
async def health_page(
    request: Request,
    app_q: str = Query("", alias="app"),
    env: str = "",
    win: str = Query("", alias="min"),
    c: InsightCtx = Depends(ctx),
) -> Response:
    data = await c.ops.read()
    rows = health_rows(data.get("health", []), int(time.time() * 1000))
    return c.render(
        request,
        "admin_health.html",
        "Operação",
        "Saúde da plataforma",
        "Serviços e integrações conectados.",
        rows=rows,
        slos=slo_rows(data.get("slos", []), c.tel.metrics, c.short),
        has_latency=any(r["latency"] != "—" for r in rows),
        slo_meta=dumps(c.app_ids_meta()),
        meta=c.meta_json(),
        update=updates.notice(request.app.state),
        **obs_ctx(c, request, app_q, env, win),
    )


@router.get("/health")
async def list_health(c: InsightCtx = Depends(ctx)) -> Any:
    return (await c.ops.read())["health"]


@router.post("/health", status_code=201)
async def create_service(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    return await c.ops.create_service(c.actor_of(request), await c.body(request))


@router.post("/health/check")
async def check_health(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    async def probe(app_id: str, upstream: str) -> tuple[str, bool, int]:
        t0 = time.monotonic()
        try:
            up = (await c.client.get(f"{upstream.rstrip('/')}/{app_id}", timeout=3)).status_code < 500
        except httpx.HTTPError:
            up = False
        return app_id, up, int((time.monotonic() - t0) * 1000)

    results = await asyncio.gather(*(probe(i, u) for i, u in c.insight.targets()))
    health = await c.ops.record_probes(c.actor_of(request), list(results))
    rows = [{"app": a, "available": up, "latency_ms": m} for a, up, m in results]
    return {"results": rows, "staging": [], "health": health}


@router.put("/health/{name}")
async def update_service(request: Request, name: str, c: InsightCtx = Depends(ctx)) -> Any:
    return await c.ops.update_service(c.actor_of(request), name, await c.body(request))


@router.delete("/health/{name}", status_code=204)
async def delete_service(request: Request, name: str, c: InsightCtx = Depends(ctx)) -> Response:
    await c.ops.delete_service(c.actor_of(request), name)
    return Response(status_code=204)


@router.get("/slos")
async def list_slos(c: InsightCtx = Depends(ctx)) -> Any:
    return (await c.store.cfg.read()).get("slos", [])


@router.put("/slos")
async def put_slo(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    return await c.ops.set_slo(c.actor_of(request), await c.body(request))


@router.delete("/slos/{app_id}/{env}")
async def delete_slo(request: Request, app_id: str, env: str, c: InsightCtx = Depends(ctx)) -> Any:
    await c.ops.delete_slo(c.actor_of(request), app_id, env)
    return {"deleted": True}


@router.get("/incidents")
async def list_incidents(c: InsightCtx = Depends(ctx)) -> Any:
    return await c.ops.incidents()


@router.post("/incidents")
async def create_incident(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    return await c.ops.create_incident(c.actor_of(request), await c.body(request))


@router.post("/incidents/{iid}")
async def update_incident(request: Request, iid: int, c: InsightCtx = Depends(ctx)) -> Any:
    b = await c.body(request)
    return await c.ops.update_incident(
        c.actor_of(request), iid, str(b.get("note", "")), bool(b.get("resolve"))
    )


# ---- Feature flags ---------------------------------------------------------------------------------
@pages.get("/admin/flags")
async def flags_page() -> Response:
    """The screen was removed from the UI (the `/api/admin/flags` API and the evaluation remain)."""
    return RedirectResponse("/admin", status_code=308)


@router.get("/flags")
async def list_flags(c: InsightCtx = Depends(ctx)) -> Any:
    flags, version = await c.ops.flags()
    return {"flags": flags, "version": version}


@router.post("/flags")
async def create_flag(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    return await c.ops.upsert_flag(c.actor_of(request), await c.body(request), True)


@router.put("/flags/{app_id}/{key}")
async def update_flag(request: Request, app_id: str, key: str, c: InsightCtx = Depends(ctx)) -> Any:
    return await c.ops.upsert_flag(
        c.actor_of(request), (await c.body(request)) | {"app": app_id, "key": key}, False
    )


@router.delete("/flags/{app_id}/{key}")
async def delete_flag(request: Request, app_id: str, key: str, c: InsightCtx = Depends(ctx)) -> Any:
    await c.ops.delete_flag(c.actor_of(request), app_id, key)
    return {"deleted": key}


@router.post("/flags/{app_id}/{key}/{env}/enabled")
async def set_flag_enabled(
    request: Request, app_id: str, key: str, env: str, c: InsightCtx = Depends(ctx)
) -> Any:
    b = await c.body(request)
    if not isinstance(b.get("enabled"), bool):
        raise AdminError("Dados inválidos: verifique os campos enviados.")
    if env not in ("staging", "producao"):
        raise AdminError("Ambiente inválido.")
    return await c.ops.set_flag_enabled(c.actor_of(request), app_id, key, env, b["enabled"])


# ---- Observabilidade -------------------------------------------------------------------------------
@pages.get("/admin/observability")
async def old_observability(request: Request) -> Response:
    q = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/admin/health{q}#obs", 308)
