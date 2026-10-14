"""Mockup pages, the directory (with the supplier record) and the CSS assets."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk.render import html

from fornecedores.state import HERE, State, StateDep
from fornecedores.suppliers import SUPPLIERS, supplier
from fornecedores.ui import esc, param
from fornecedores.views import detail, not_found

CSS = "text/css; charset=utf-8"
SECTIONS = [
    s.strip() for s in (HERE / "fragments" / "sections.txt").read_text("utf-8").splitlines() if s.strip()
]

router = APIRouter()


def shared_assets_dir() -> Path:
    """Shared ``demo.css``: ``FORNECEDORES_SHARED_ASSETS`` or ``platform/demo-apps/assets``."""
    env = os.environ.get("FORNECEDORES_SHARED_ASSETS", "")
    return Path(env) if env else HERE.parents[3] / "demo-apps" / "assets"


def static(st: State, name: str, req: Request) -> Response:
    body = st.fragment(name, req) if name in SECTIONS or name == "_skeleton" else None
    return html(body) if body is not None else not_found()


def linked_rows(page: str) -> str:
    """Turns the "···" of each row into a link to the supplier record."""
    for s in SUPPLIERS:
        old = (
            f'<button class="app2-more" aria-label="Mais ações" '
            f'data-hub-toast="Detalhes de {esc(s.name)} selecionados.">···</button>'
        )
        new = (
            f'<a class="app2-more" aria-label="Abrir ficha de {esc(s.name)}" '
            f'href="/apps/fornecedores/indicators?id={esc(s.code)}">···</a>'
        )
        page = page.replace(old, new)
    return page


@router.get("/fornecedores")
@router.get("/fornecedores/")
async def dashboard(req: Request, st: StateDep) -> Response:
    return static(st, "dashboard", req)


@router.get("/fornecedores/_skeleton")
async def skeleton(req: Request, st: StateDep) -> Response:
    return static(st, "_skeleton", req)


@router.get("/fornecedores/assets/{name}")
async def asset(name: str) -> Response:
    p = shared_assets_dir() / "demo.css" if name == "demo.css" else HERE / "assets" / "app.css"
    if name not in ("demo.css", "app.css"):
        return Response(status_code=404)
    try:
        body = p.read_bytes()
    except OSError:
        return Response(status_code=404)
    return Response(body, media_type=CSS, headers={"cache-control": "no-cache"})


@router.get("/fornecedores/indicators")
async def directory(req: Request, st: StateDep) -> Response:
    page = st.fragment("indicators", req)
    if page is None:
        return not_found()
    page = linked_rows(page)
    if sup := supplier(param(dict(req.query_params), "id")):
        marker = '<div class="app2-directory-summary">'
        page = page.replace(marker, f"{await detail(st, sup)}{marker}", 1)
    return html(page)


# Catch-all section: registered last (``app.create_app`` includes this router after the others).
@router.get("/fornecedores/{section}")
async def section(section: str, req: Request, st: StateDep) -> Response:
    return static(st, section, req)
