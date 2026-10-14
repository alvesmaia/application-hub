"""Skeleton, CSS assets, Diagnostics and the catch-all for unknown sections."""

from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response
from hub_sdk import __version__ as SDK_VERSION
from hub_sdk.identity import env_of

from coletor.state import HERE, StateDep

ASSETS = {
    "app.css": ("app.css", "text/css; charset=utf-8"),
    "demo.css": ("demo.css", "text/css; charset=utf-8"),
}

router = APIRouter()


def shared_assets_dir() -> Path:
    env = os.environ.get("COLETOR_SHARED_ASSETS", "")
    if env:
        return Path(env)
    return HERE.parents[3] / "demo-apps" / "assets"


@router.get("/coletor/_skeleton")
async def skeleton(st: StateDep) -> Response:
    return st.render.skeleton()


@router.get("/coletor/assets/{name}")
async def asset(name: str) -> Response:
    a = ASSETS.get(name)
    if a is None:
        return Response(status_code=404)
    file, mime = a
    base = shared_assets_dir() if name == "demo.css" else HERE / "assets"
    try:
        body = await asyncio.to_thread((base / file).read_bytes)
    except OSError:
        return Response(status_code=404)
    return Response(body, media_type=mime, headers={"cache-control": "no-cache"})


@router.get("/coletor/diagnostico")
async def diagnostico(req: Request, st: StateDep) -> Response:
    return st.render.page(
        req,
        "diagnostico.html",
        "diagnostico",
        py=platform.python_version(),
        sdk=SDK_VERSION,
        version=st.sdk.version,
        env=env_of(req),
        db_ready=st.sdk.db_ready,
    )


# Catch-all section: ``app.create_app`` includes this router after the others.
@router.get("/coletor/{section}")
async def other(section: str, st: StateDep) -> Response:
    return st.render.not_found()
