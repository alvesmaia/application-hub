"""Pages and APIs for Logs, Audit, Health/SLOs, Feature flags, Observability and Analytics (`/admin/*`).

Wires the shared state (`InsightCtx`) and mounts the section modules (`routes_*`)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from hub.admin.routes import json_only
from hub.insight import routes_analytics, routes_health, routes_logs
from hub.insight.insightctx import InsightCtx
from hub.insight.metrics import enabled
from hub.insight.runtime import Insight
from hub.insight.view import chart as chart
from hub.insight.view import ms_text as ms_text
from hub.insight.view import split_ids as split_ids

_SECTIONS = (routes_logs, routes_health, routes_analytics)


async def api_spans(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if (
        not enabled()
        or request.method in ("GET", "HEAD", "OPTIONS")
        or not request.url.path.startswith("/api/")
    ):
        return await call_next(request)
    t0, us = time.monotonic(), time.time_ns() // 1000
    resp = await call_next(request)
    request.app.state.insight_ctx.insight.span_for_api(request, resp.status_code, t0, us)
    return resp


def mount_insight(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
    insight: Insight,
    client: httpx.AsyncClient,
) -> None:
    app.state.insight_ctx = InsightCtx(app, templates, page_ctx, icon, insight, client)
    api = APIRouter(prefix="/api/admin", dependencies=[Depends(json_only)])
    for section in _SECTIONS:
        app.include_router(section.pages)
        api.include_router(section.router)
    app.middleware("http")(api_spans)
    app.include_router(api)
