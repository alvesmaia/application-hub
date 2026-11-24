"""Mockup pages, the directory (with the supplier record) and the CSS assets."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.render import html

from suppliers.contracts import restatus, today
from suppliers.state import HERE, State, StateDep, base_of
from suppliers.suppliers import SUPPLIERS, supplier
from suppliers.ui import esc, param
from suppliers.views import detail, not_found

CSS = "text/css; charset=utf-8"
SECTIONS = [
    s.strip() for s in (HERE / "fragments" / "sections.txt").read_text("utf-8").splitlines() if s.strip()
]

router = APIRouter()


def shared_assets_dir() -> Path:
    """Shared ``demo.css``: ``SUPPLIERS_SHARED_ASSETS`` or ``platform/demo-apps/assets``."""
    env = os.environ.get("SUPPLIERS_SHARED_ASSETS", "")
    return Path(env) if env else HERE.parents[3] / "demo-apps" / "assets"


def static(st: State, name: str, req: Request) -> Response:
    body = st.fragment(name, req) if name in SECTIONS or name == "_skeleton" else None
    return html(body) if body is not None else not_found(base_of(req))


def linked_rows(page: str, base: str) -> str:
    """Turns the "···" of each row into a link to the supplier record."""
    for s in SUPPLIERS:
        old = (
            f'<button class="app2-more" aria-label="Mais ações" '
            f'data-hub-toast="Detalhes de {esc(s.name)} selecionados.">···</button>'
        )
        new = (
            f'<a class="app2-more" aria-label="Abrir ficha de {esc(s.name)}" '
            f'href="{base}/indicators?id={esc(s.code)}">···</a>'
        )
        page = page.replace(old, new)
    return page


@router.get("/suppliers")
@router.get("/suppliers/")
async def dashboard(req: Request, st: StateDep) -> Response:
    return static(st, "dashboard", req)


@router.get("/suppliers/_skeleton")
async def skeleton(req: Request, st: StateDep) -> Response:
    return static(st, "_skeleton", req)


@router.get("/suppliers/assets/{name}")
async def asset(name: str) -> Response:
    p = shared_assets_dir() / "demo.css" if name == "demo.css" else HERE / "assets" / "app.css"
    if name not in ("demo.css", "app.css"):
        return Response(status_code=404)
    try:
        body = p.read_bytes()
    except OSError:
        return Response(status_code=404)
    return Response(body, media_type=CSS, headers={"cache-control": "no-cache"})


@router.get("/suppliers/indicators")
async def directory(req: Request, st: StateDep) -> Response:
    page, base = st.fragment("indicators", req), base_of(req)
    if page is None:
        return not_found(base)
    page = linked_rows(page, base)
    if sup := supplier(param(dict(req.query_params), "id")):
        marker = '<div class="app2-directory-summary">'
        page = page.replace(marker, f"{await detail(st, sup, base)}{marker}", 1)
    return html(page)


@router.get("/suppliers/reports")
async def contracts(req: Request, st: StateDep) -> Response:
    page = st.fragment("reports", req)
    return html(restatus(page, today())) if page is not None else not_found(base_of(req))


# Catch-all section: registered last (``app.create_app`` includes this router after the others).
@router.get("/suppliers/{section}")
async def section(section: str, req: Request, st: StateDep) -> Response:
    return static(st, section, req)
