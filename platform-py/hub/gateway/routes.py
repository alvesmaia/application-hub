"""Gateway routes: /_frag, /apps/{id}[/{section}], /apps/{id}/api, /apps/{id}/ws, legacy redirects."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from fastapi import APIRouter, Depends, FastAPI, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import HTTPConnection

from hub.auth import COOKIE, permissions
from hub.gateway.csp import approved_scripts, csp_with, sandbox_fragment
from hub.gateway.env_path import env_of, public
from hub.gateway.proxy import Gateway
from hub.gateway.registry import AppEntry
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
    env = env_of(request.scope)
    csp = csp_with(origin_of(request), app, approved_scripts(app, env == "staging"), env)
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


def _env_page(request: Request, c: GwCtx, entry: AppEntry) -> Response | None:
    """The staging cookie sends the production URL to the staging one; the staging URL needs the permission."""
    if env_of(request.scope) == "producao":
        if request.cookies.get("hub_env") != "staging":
            return None
        query = request.url.query
        return RedirectResponse(public(request.url.path, "staging") + (f"?{query}" if query else ""), 303)
    staged = c.gw.staging_target(request, entry, c.gw.identity_for(request))
    return staged if isinstance(staged, Response) else None


def _segments_ok(section: str, rest: str) -> bool:
    """`<section>[/<a>/<b>…]`: up to 8 segments of letters, digits, `-` and `_` (64 each); a trailing `/` is fine."""
    parts = [section, *[x for x in rest.split("/") if x]]
    return len(parts) <= 8 and all(
        x.replace("-", "").replace("_", "").isalnum() and len(x) <= 64 for x in parts
    )


@router.get("/apps/{app_id}", response_class=HTMLResponse)
@router.get("/apps/{app_id}/{section}", response_class=HTMLResponse)
@router.get(
    "/apps/{app_id}/{section}/{rest:path}", response_class=HTMLResponse
)  # deep links of nested routes
async def page(
    request: Request, app_id: str, section: str = "dashboard", rest: str = "", c: GwCtx = Depends(ctx)
) -> Response:
    entry = c.gw.registry.get(app_id)
    if entry is None or not _segments_ok(
        section, rest
    ):  # HTML (not an empty body): the browser does not treat it as a download
        nf = page_response(c.templates, request, "not_found.html", c.page_ctx(request))
        nf.status_code = 404
        return nf
    if (moved := _env_page(request, c, entry)) is not None:
        return moved
    d, env = entry.display, env_of(request.scope)
    frag = public("/_frag", env)
    scripts = [
        {"src": f"{frag}/{app_id}/assets/{f}", "integrity": s}
        for f, s in approved_scripts(app_id, env == "staging")
    ]
    data = c.page_ctx(request) | {
        "path": f"/apps/{app_id}",
        "app": {
            "frag": frag,
            "base": public(f"/apps/{app_id}", env),
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
