"""Mockup pages (dashboard with the feature-flag banner), the skeleton and the CSS assets."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk import flags
from hub_sdk.render import html

from ativos.state import HERE, State, StateDep, base_of
from ativos.views import not_found

FLAG_NOVO_RESUMO = "novo-resumo"
CSS = "text/css; charset=utf-8"
SECTIONS = [
    s.strip() for s in (HERE / "fragments" / "sections.txt").read_text("utf-8").splitlines() if s.strip()
]

router = APIRouter()


def shared_assets_dir() -> Path:
    """Shared ``demo.css``: ``ATIVOS_SHARED_ASSETS`` or ``platform/demo-apps/assets``."""
    env = os.environ.get("ATIVOS_SHARED_ASSETS", "")
    return Path(env) if env else HERE.parents[3] / "demo-apps" / "assets"


def dashboard_page(st: State, req: Request) -> Response:
    body = st.fragment("dashboard", req)
    if body is None:
        return not_found(base_of(req))
    # Wave 8 — demo feature flag `novo-resumo` (Platform › Feature flags): the
    # hub evaluates it for the user and sends it in `x-hub-flags`.
    if flags.from_headers(req.headers).is_on(FLAG_NOVO_RESUMO):
        banner = (
            '<div class="hub-flag-banner" data-flag="novo-resumo" role="note"><strong>Novo resumo</strong> — 1.106 '
            "ativos em uso, 23 em manutenção e 3 precisando de atenção hoje. "
            "<small>(recurso liberado por feature flag)</small></div>"
        )
        body = body.replace('<div class="app-page-header">', f'{banner}<div class="app-page-header">', 1)
    return html(body)


@router.get("/ativos")
@router.get("/ativos/")
@router.get("/ativos/dashboard")
async def dashboard(req: Request, st: StateDep) -> Response:
    return dashboard_page(st, req)


@router.get("/ativos/_skeleton")
async def skeleton(req: Request, st: StateDep) -> Response:
    body = st.fragment("_skeleton", req)
    return html(body) if body is not None else not_found(base_of(req))


@router.get("/ativos/assets/{name}")
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
@router.get("/ativos/{section}")
async def other(req: Request, section: str, st: StateDep) -> Response:
    if section not in SECTIONS:
        return not_found(base_of(req))
    if section == "dashboard":
        return dashboard_page(st, req)
    body = st.fragment(section, req)
    return html(body) if body is not None else not_found(base_of(req))
