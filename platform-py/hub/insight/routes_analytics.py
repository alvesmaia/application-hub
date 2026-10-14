"""Usage analytics (summary, CSV, retention, terms, key rotation) and usage per user (page, API, CSV)."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from hub.admin.config import AdminError
from hub.admin.routes import relative
from hub.insight.insightctx import InsightCtx, ctx
from hub.insight.usage import PLATFORM
from hub.ux.pager import paginate

pages = APIRouter()
router = APIRouter()  # under /api/admin (JSON only)


# ---- Analytics -------------------------------------------------------------------------------------
@pages.get("/admin/analytics")
async def old_analytics(request: Request) -> Response:
    # Whoever only has view_audit (Product Owner) sees the usage card, read-only, in Audit.
    can_cfg = "editar_configuracoes" in getattr(request.state, "permissions", [])
    return RedirectResponse("/admin#uso" if can_cfg else "/admin/audit#uso", 308)


@router.get("/analytics")
async def analytics_get(c: InsightCtx = Depends(ctx)) -> Any:
    an = c.an
    s = an.summary(int(time.time() * 1000))
    prefs = c.app.state.ux.data.users
    return {
        "summary": s,
        "retention_days": an.retention,
        "raw_terms": an.raw_terms,
        "enabled": an.enabled,
        "opted_out": sum(1 for p in prefs.values() if p.analytics_opt_out),
        "storage_bytes": an.bytes_estimate(),
    }


@pages.get("/api/admin/analytics.csv")
async def analytics_csv(c: InsightCtx = Depends(ctx)) -> Response:
    return Response(
        "﻿" + c.an.csv(int(time.time() * 1000)),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": 'attachment; filename="analytics-de-uso.csv"'},
    )


@router.put("/analytics/retention")
async def analytics_retention(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    c.need_settings(request)
    b = await c.body(request)
    if not isinstance(b.get("days"), int) or isinstance(b["days"], bool):
        raise AdminError("Dados inválidos: verifique os campos enviados.")
    days = c.an.set_retention(b["days"])
    await c.store.audit(
        c.actor_of(request), "Retenção do analytics alterada", f"{days} dias", icon="settings"
    )
    return {"ok": True, "days": days}


@router.put("/analytics/terms")
async def analytics_terms(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    c.need_settings(request)
    b = await c.body(request)
    if not isinstance(b.get("raw"), bool):
        raise AdminError("Dados inválidos: verifique os campos enviados.")
    c.an.set_raw_terms(b["raw"])
    detail = "texto guardado (opt-in)" if b["raw"] else "só contagem e hash"
    await c.store.audit(c.actor_of(request), "Termos de busca sem resultado", detail, icon="settings")
    return {"ok": True, "raw": b["raw"]}


@router.post("/analytics/rotate-key")
async def analytics_rotate(request: Request, c: InsightCtx = Depends(ctx)) -> Any:
    c.need_settings(request)
    c.an.rotate_key(int(time.time() * 1000))
    await c.store.audit(
        c.actor_of(request),
        "Chave dos ids do analytics trocada",
        "ids novos não se ligam aos anteriores",
        icon="settings",
    )
    return {"ok": True}


# ---- Usage per user ---------------------------------------------------------------------------------
async def _usage_query(c: InsightCtx, days: int, user: str, app_id: str) -> dict[str, Any]:
    q = c.us.query(int(time.time() * 1000), days, user.strip(), app_id.strip())
    async with c.app.state.auth.locked() as d:
        people = {u["id"]: (u.get("name") or "", u.get("email") or "") for u in d["users"]}
    names = {a["id"]: a["short"] for a in c.refs()} | {PLATFORM: "Hub"}
    for r in q["rows"] + q["by_user"] + q["by_app"]:
        if "user" in r:
            r["name"], r["email"] = people.get(r["user"], ("", ""))
        if "app" in r:
            r["app_name"] = names.get(r["app"], r["app"])
    return q | {"enabled": c.us.enabled}


@router.get("/usage")
async def usage_get(
    days: int = 30, user: str = "", app_id: str = Query("", alias="app"), c: InsightCtx = Depends(ctx)
) -> Any:
    return await _usage_query(c, days, user, app_id)


@pages.get("/api/admin/usage.csv")
async def usage_csv(
    days: int = 30, user: str = "", app_id: str = Query("", alias="app"), c: InsightCtx = Depends(ctx)
) -> Response:
    return Response(
        "﻿" + c.us.csv(int(time.time() * 1000), days, user.strip(), app_id.strip()),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": 'attachment; filename="uso-por-usuario.csv"'},
    )


@pages.get("/admin/usage", response_class=HTMLResponse)
async def usage_page(
    request: Request,
    days: int = 30,
    user: str = "",
    app_id: str = Query("", alias="app"),
    c: InsightCtx = Depends(ctx),
) -> Response:
    days = days if days in (7, 30, 90) else 30
    q = await _usage_query(c, days, user, app_id)
    now = int(time.time() * 1000)
    for r in q["rows"] + q["by_user"] + q["by_app"]:
        r["when"] = relative(r["last"], now)
    rows, pager = paginate(request, q["rows"])
    async with c.app.state.auth.locked() as d:
        people = sorted((u["id"], u.get("name") or u.get("email") or u["id"]) for u in d["users"])
    apps = [(a["id"], a["short"]) for a in c.refs()] + [(PLATFORM, "Hub")]
    return c.render(
        request,
        "admin_usage.html",
        "Governança",
        "Uso por usuário",
        "Quem usa cada aplicação: aberturas, navegações, ações e último acesso, coletados pelo gateway.",
        q=q,
        rows=rows,
        pager=pager,
        days=days,
        user=user,
        app_id=app_id,
        people=sorted(people, key=lambda p: p[1].lower()),
        apps=apps,
    )
