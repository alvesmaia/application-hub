"""Mockup pages, the skeleton and the CSS assets."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.render import html

from servicedesk import views
from servicedesk.state import HERE, State, StateDep, base_of, db_notice
from servicedesk.views import not_found

CSS = "text/css; charset=utf-8"
SECTIONS = [
    s.strip() for s in (HERE / "fragments" / "sections.txt").read_text("utf-8").splitlines() if s.strip()
]

router = APIRouter()


def shared_assets_dir() -> Path:
    """``demo.css`` shared: ``SERVICEDESK_SHARED_ASSETS`` or ``platform/demo-apps/assets``."""
    env = os.environ.get("SERVICEDESK_SHARED_ASSETS", "")
    return Path(env) if env else HERE.parents[3] / "demo-apps" / "assets"


def static(st: State, name: str, req: Request) -> Response:
    body = st.page(name, req) if name in SECTIONS else None
    return html(body) if body is not None else not_found(base_of(req))


async def dashboard_page(st: State, req: Request) -> Response:
    body = st.page("dashboard", req)
    if body is None:
        return not_found(base_of(req))
    if db_notice(st) is None:
        start = body.find('<div class="app3-kpi-row">') + len('<div class="app3-kpi-row">')
        end = body.find("<article><span>Tempo médio", start)
        body = body[:start] + views.kpi_articles(await st.status_counts()) + body[end:]
    return html(body)


@router.get("/servicedesk")
@router.get("/servicedesk/")
async def dashboard(req: Request, st: StateDep) -> Response:
    return await dashboard_page(st, req)


@router.get("/servicedesk/_skeleton")
async def skeleton(req: Request, st: StateDep) -> Response:
    body = st.skeleton(req)
    return html(body) if body is not None else not_found(base_of(req))


@router.get("/servicedesk/assets/{name}")
async def asset(name: str) -> Response:
    if name == "demo.css":
        p = shared_assets_dir() / "demo.css"
    elif name == "app.css":
        p = HERE / "assets" / "app.css"
    else:
        return Response(status_code=404)
    try:
        body = p.read_bytes()
    except OSError:
        return Response(status_code=404)
    return Response(body, media_type=CSS, headers={"cache-control": "no-cache"})


# Catch-all section: ``app.create_app`` includes this router after the others.
@router.get("/servicedesk/{section}")
async def other(req: Request, section: str, st: StateDep) -> Response:
    if section == "dashboard":
        return await dashboard_page(st, req)
    return static(st, section, req)
