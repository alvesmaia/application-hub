"""Mockup pages, the skeleton and the CSS assets."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.render import html

from servicos.state import HERE, State, StateDep, base_of
from servicos.views import not_found

CSS = "text/css; charset=utf-8"
SECTIONS = [
    s.strip() for s in (HERE / "fragments" / "sections.txt").read_text("utf-8").splitlines() if s.strip()
]

router = APIRouter()


def shared_assets_dir() -> Path:
    """``demo.css`` shared: ``SERVICOS_SHARED_ASSETS`` or ``platform/demo-apps/assets``."""
    env = os.environ.get("SERVICOS_SHARED_ASSETS", "")
    return Path(env) if env else HERE.parents[3] / "demo-apps" / "assets"


def static(st: State, name: str, req: Request) -> Response:
    body = st.page(name, req) if name in SECTIONS else None
    return html(body) if body is not None else not_found(base_of(req))


@router.get("/servicos")
@router.get("/servicos/")
async def dashboard(req: Request, st: StateDep) -> Response:
    return static(st, "dashboard", req)


@router.get("/servicos/_skeleton")
async def skeleton(req: Request, st: StateDep) -> Response:
    body = st.skeleton(req)
    return html(body) if body is not None else not_found(base_of(req))


@router.get("/servicos/assets/{name}")
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
@router.get("/servicos/{section}")
async def other(req: Request, section: str, st: StateDep) -> Response:
    return static(st, section, req)
