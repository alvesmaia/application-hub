"""Rotas do gateway: /_frag, /apps/{id}[/{secao}], /apps/{id}/api, /apps/{id}/ws, redirecionamentos antigos."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

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


def mount_gateway(
    app: FastAPI,
    gw: Gateway,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    observe: Callable[[Request, str, Callable[[], Awaitable[Response]]], Awaitable[Response]] | None = None,
) -> None:
    r = APIRouter()

    async def frag(request: Request, app_id: str, rest: str) -> Response:
        call = lambda: gw.forward(request, app_id, rest)  # noqa: E731
        resp = await (observe(request, app_id, call) if observe else call())
        if gw.registry.get(app_id):
            _app_headers(request, resp, app_id, frag=True)
        return resp

    @r.api_route("/_frag/{app_id}", methods=ALL)
    async def frag_root(request: Request, app_id: str) -> Response:
        return await frag(request, app_id, "")

    @r.api_route("/_frag/{app_id}/{rest:path}", methods=ALL)
    async def frag_rest(request: Request, app_id: str, rest: str) -> Response:
        return await frag(request, app_id, rest)

    @r.api_route("/apps/{app_id}/api/{rest:path}", methods=ALL)
    async def app_api(request: Request, app_id: str, rest: str) -> Response:
        return await gw.forward(request, app_id, rest)

    @r.websocket("/apps/{app_id}/ws")
    async def ws_root(ws: WebSocket, app_id: str) -> None:
        if not await _ws_user(ws, app_id):
            await ws.close(code=1008)
            return
        await proxy_ws(gw, ws, app_id, "/ws")

    @r.websocket("/apps/{app_id}/ws/{rest:path}")
    async def ws_rest(ws: WebSocket, app_id: str, rest: str) -> None:
        if not await _ws_user(ws, app_id):
            await ws.close(code=1008)
            return
        await proxy_ws(gw, ws, app_id, f"/ws/{rest}")

    async def _ws_user(ws: WebSocket, app_id: str) -> bool:
        if facade := ws.scope.get("hub_facade"):  # `hub dev`: usuário simulado em processo
            user = facade[0]
        elif not (token := ws.cookies.get(COOKIE)):
            return False
        else:
            try:
                user = await ws.app.state.auth.session(token)
            except Exception:
                return False
        if not user:
            return False
        perms, apps = permissions(user, ws.app.state.roles)
        if "acessar_aplicacoes" not in perms or (apps is not None and app_id not in apps):
            return False
        ws.state.user, ws.state.permissions, ws.state.apps = user, perms, apps
        return True

    @r.get("/apps/{app_id}", response_class=HTMLResponse)
    @r.get("/apps/{app_id}/{section}", response_class=HTMLResponse)
    async def page(request: Request, app_id: str, section: str = "dashboard") -> Response:
        entry = gw.registry.get(app_id)
        ok = section.replace("-", "").replace("_", "").isalnum() and len(section) <= 64
        if entry is None or not ok:  # HTML (não corpo vazio): o navegador não trata como download
            nf = page_response(templates, request, "not_found.html", page_ctx(request))
            nf.status_code = 404
            return nf
        d = entry.display
        scripts = [
            {"src": f"/_frag/{app_id}/assets/{f}", "integrity": s} for f, s in approved_scripts(app_id, False)
        ]
        ctx = page_ctx(request) | {
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
        resp = templates.TemplateResponse(request, "app.html", ctx)
        _app_headers(request, resp, app_id, frag=False)
        return resp

    app.include_router(r)

    @app.middleware("http")
    async def legacy_redirect(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """`/{id}/...` antigo de app registrado -> `/apps/{id}/...` (301 GET/HEAD, 308 demais)."""
        seg = request.url.path.lstrip("/").split("/", 1)[0]
        if seg and seg != "apps" and gw.registry.get(seg) is not None:
            raw = (request.scope.get("raw_path") or request.url.path.encode()).decode("latin-1")
            q = f"?{request.url.query}" if request.url.query else ""
            status = 301 if request.method in ("GET", "HEAD") else 308
            return Response(status_code=status, headers={"location": f"/apps{raw}{q}"})
        return await call_next(request)
