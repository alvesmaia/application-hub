"""Gateway routes: /_frag, /apps/{id}[/{section}], /apps/{id}/api, /apps/{id}/ws, legacy redirects."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from fastapi import APIRouter, Depends, FastAPI, Request, Response, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import HTTPConnection

from hub.auth import COOKIE, permissions
from hub.gateway.csp import approved_scripts, csp_with, sandbox_fragment
from hub.gateway.proxy import Gateway
from hub.gateway.ws import proxy_ws
from hub.ux.texts import page_response

ALL = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def origin_of(request: Request) -> str | None:
    host = request.headers.get("host")
    if not host:
        return None
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{proto}://{host}"


def _app_headers(request: Request, resp: Response, app: str, frag: bool) -> None:
    csp = csp_with(origin_of(request), app, approved_scripts(app, False))
    resp.headers["content-security-policy"] = sandbox_fragment(csp) if frag else csp
    resp.headers["vary"] = "Cookie"
    if "cache-control" not in resp.headers:
        resp.headers["cache-control"] = "private, no-store"


Observe = Callable[[Request, str, Callable[[], Awaitable[Response]]], Awaitable[Response]]


@dataclass
class GwCtx:
    """What the gateway routes share (`app.state.gw_ctx`); handlers get it with `Depends(ctx)`."""

    gw: Gateway
    templates: Jinja2Templates
    page_ctx: Callable[[Request], dict[str, Any]]
    observe: Observe | None


def ctx(conn: HTTPConnection) -> GwCtx:
    return cast(GwCtx, conn.app.state.gw_ctx)


router = APIRouter()


async def frag(request: Request, c: GwCtx, app_id: str, rest: str) -> Response:
    call = lambda: c.gw.forward(request, app_id, rest)  # noqa: E731
    resp = await (c.observe(request, app_id, call) if c.observe else call())
    if c.gw.registry.get(app_id):
        _app_headers(request, resp, app_id, frag=True)
    return resp


@router.api_route("/_frag/{app_id}", methods=ALL)
async def frag_root(request: Request, app_id: str, c: GwCtx = Depends(ctx)) -> Response:
    return await frag(request, c, app_id, "")


@router.api_route("/_frag/{app_id}/{rest:path}", methods=ALL)
async def frag_rest(request: Request, app_id: str, rest: str, c: GwCtx = Depends(ctx)) -> Response:
    return await frag(request, c, app_id, rest)


@router.api_route("/apps/{app_id}/api/{rest:path}", methods=ALL)
async def app_api(request: Request, app_id: str, rest: str, c: GwCtx = Depends(ctx)) -> Response:
    return await c.gw.forward(request, app_id, rest)


@router.websocket("/apps/{app_id}/ws")
async def ws_root(ws: WebSocket, app_id: str, c: GwCtx = Depends(ctx)) -> None:
    if not await _ws_user(ws, app_id):
        await ws.close(code=1008)
        return
    await proxy_ws(c.gw, ws, app_id, "/ws")


@router.websocket("/apps/{app_id}/ws/{rest:path}")
async def ws_rest(ws: WebSocket, app_id: str, rest: str, c: GwCtx = Depends(ctx)) -> None:
    if not await _ws_user(ws, app_id):
        await ws.close(code=1008)
        return
    await proxy_ws(c.gw, ws, app_id, f"/ws/{rest}")


async def _ws_session(ws: WebSocket) -> dict[str, Any] | None:
    if facade := ws.scope.get("hub_facade"):  # `hub dev`: simulated user in-process
        return cast(dict[str, Any] | None, facade[0])
    if not (token := ws.cookies.get(COOKIE)):
        return None
    try:
        return cast(dict[str, Any] | None, await ws.app.state.auth.session(token))
    except Exception:
        return None


async def _ws_user(ws: WebSocket, app_id: str) -> bool:
    user = await _ws_session(ws)
    if not user:
        return False
    perms, apps = permissions(user, ws.app.state.roles)
    if "acessar_aplicacoes" not in perms or (apps is not None and app_id not in apps):
        return False
    ws.state.user, ws.state.permissions, ws.state.apps = user, perms, apps
    return True


@router.get("/apps/{app_id}", response_class=HTMLResponse)
@router.get("/apps/{app_id}/{section}", response_class=HTMLResponse)
async def page(
    request: Request, app_id: str, section: str = "dashboard", c: GwCtx = Depends(ctx)
) -> Response:
    entry = c.gw.registry.get(app_id)
    ok = section.replace("-", "").replace("_", "").isalnum() and len(section) <= 64
    if entry is None or not ok:  # HTML (not an empty body): the browser does not treat it as a download
        nf = page_response(c.templates, request, "not_found.html", c.page_ctx(request))
        nf.status_code = 404
        return nf
    d = entry.display
    scripts = [
        {"src": f"/_frag/{app_id}/assets/{f}", "integrity": s} for f, s in approved_scripts(app_id, False)
    ]
    data = c.page_ctx(request) | {
        "path": f"/apps/{app_id}",
        "app": {
            "id": app_id,
            "name": entry.name,
            "version": entry.version,
            "section": section,
            "color": d.get("color", "#e0f2f1"),
            "ink": d.get("ink", "#0f766e"),
            "icon": d.get("icon", "box"),
            "scripts": json.dumps(scripts),
        },
    }
    resp = c.templates.TemplateResponse(request, "app.html", data)
    _app_headers(request, resp, app_id, frag=False)
    return resp


async def legacy_redirect(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Legacy `/{id}/...` of a registered app -> `/apps/{id}/...` (301 for GET/HEAD, 308 for the rest)."""
    seg = request.url.path.lstrip("/").split("/", 1)[0]
    if seg and seg != "apps" and ctx(request).gw.registry.get(seg) is not None:
        raw = (request.scope.get("raw_path") or request.url.path.encode()).decode("latin-1")
        q = f"?{request.url.query}" if request.url.query else ""
        status = 301 if request.method in ("GET", "HEAD") else 308
        return Response(status_code=status, headers={"location": f"/apps{raw}{q}"})
    return await call_next(request)


def mount_gateway(
    app: FastAPI,
    gw: Gateway,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    observe: Observe | None = None,
) -> None:
    app.state.gw_ctx = GwCtx(gw, templates, page_ctx, observe)
    app.include_router(router)
    app.middleware("http")(legacy_redirect)
