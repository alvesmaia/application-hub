"""Home (catalog of enabled processes), skeleton, CSS assets and the 404 for unknown sections."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response

from automacoes import store, views
from automacoes.access import visible_login
from automacoes.state import DB_ERRORS, HERE, StateDep

ASSETS = ("app.css", "demo.css")

router = APIRouter()


def shared_assets_dir() -> Path:
    """Shared ``demo.css``: ``AUTOMACOES_SHARED_ASSETS`` (image) or ``platform/demo-apps/assets``."""
    env = os.environ.get("AUTOMACOES_SHARED_ASSETS", "")
    return Path(env) if env else HERE.parents[3] / "demo-apps" / "assets"


@router.get("/automacoes")
@router.get("/automacoes/")
@router.get("/automacoes/catalogo")
async def home(req: Request, st: StateDep) -> Response:
    items: list[views.FormSpec] = []
    last: dict[int, store.Run] = {}
    if st.db_ok():
        try:
            items = await views.catalog(st.pool)
            last = await store.last_runs(st.pool, visible_login(req))
        except DB_ERRORS:
            pass
    return st.page(req, "catalogo.html", "catalogo", items=items, last=last)


@router.get("/automacoes/_skeleton")
async def skeleton(st: StateDep) -> Response:
    return st.render.skeleton()


@router.get("/automacoes/assets/{name}")
async def asset(name: str) -> Response:
    if name not in ASSETS:
        return Response(status_code=404)
    base = shared_assets_dir() if name == "demo.css" else HERE / "assets"
    try:
        body = await asyncio.to_thread((base / name).read_bytes)
    except OSError:
        return Response(status_code=404)
    return Response(body, media_type="text/css; charset=utf-8", headers={"cache-control": "no-cache"})


# Catch-all section: ``app.create_app`` includes this router after the others.
@router.get("/automacoes/{section}")
async def other(section: str, st: StateDep) -> Response:
    return st.render.not_found()
