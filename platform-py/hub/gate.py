"""Authentication middleware: who is calling (facade, bearer token, session cookie), CSRF guard and route permissions."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any, cast
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from hub.api.cors import machine_public
from hub.auth import (
    COOKIE,
    AuthStore,
    admin_required,
    api_required,
    control_allowed,
    owned_surface,
    permissions,
    token_action,
    token_covers,
)
from hub.ux.shell import eyebrow_for
from hub.ux.texts import resolve

_PUBLIC_PREFIXES = (
    "/assets/",
    "/healthz",
    "/readyz",
    "/hub-sw.js",
    "/manifest.webmanifest",
    "/offline",
    "/dev/cli/",
)
_SAFE = ("GET", "HEAD", "OPTIONS")
Principal = tuple[dict[str, Any] | None, dict[str, Any] | None]


async def authentication(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    path, method = request.url.path, request.method
    request.state.lang = resolve("", request.headers.get("accept-language"))
    request.state.theme = "light"
    mcp = request.app.state.apicfg.mcp
    if machine_public(path, mcp.path):  # discovery/token/register: no session
        if not control_allowed(request, mcp.public_network, mcp.path):
            return Response("Acesso negado: origem não permitida.", status_code=403)
        return await call_next(request)
    if path == "/login" or path.startswith(("/auth/entra/",)) or path.startswith(_PUBLIC_PREFIXES):
        return await call_next(request)
    facade = request.scope.get("hub_facade")  # in-process call from API v1/MCP (already authorized)
    authz = request.headers.get("authorization", "")
    got = await _identify(request, facade, authz)
    if isinstance(got, Response):
        return got
    user, token = got
    if not user and _anonymous_ok(request, path, method, authz):
        return await call_next(request)
    if not user or (token is None and authz):
        return _unauthenticated(request, path, authz)
    if method not in _SAFE and not facade and _cross_site(request, token):
        return Response("Requisição bloqueada.", status_code=403)
    perms, apps = _set_principal(request, user, token)
    if _denied(path, method, token, perms, apps):
        return _forbidden(request, path)
    return await _proceed(request, call_next, user, path, facade)


async def _proceed(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    user: dict[str, Any],
    path: str,
    facade: Any,
) -> Response:
    """Authorized: a temporary password must be changed first; the request is then tracked (not facade calls)."""
    if user.get("must_change_password") and path != "/account/password" and path != "/logout":
        return RedirectResponse("/account/password", status_code=303)
    response = await call_next(request)
    if not facade:
        request.app.state.ux_track(request, response.status_code)
    return response


async def _identify(request: Request, facade: Any, authz: str) -> Principal | Response:
    cfg = request.app.state.apicfg
    if facade:
        user, token = facade
        return user, token
    if not control_allowed(request, cfg.mcp.public_network, cfg.mcp.path):
        return Response("Acesso negado: origem não permitida.", status_code=403)
    if authz.lower().startswith("bearer "):
        return await _bearer(request, authz[7:].strip())
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return None, None
    try:
        return await cast(AuthStore, request.app.state.auth).session(cookie), None
    except Exception:
        return Response("State database unavailable", status_code=503)


async def _bearer(request: Request, value: str) -> Principal:
    """Token user: its owner (personal token) or a synthetic user (CI token); a token outside its scope is dropped."""
    method, path, mcp_path = request.method, request.url.path, request.app.state.apicfg.mcp.path
    store = cast(AuthStore, request.app.state.auth)
    if value.startswith("hubo_"):
        token = (
            await request.app.state.oauth.verify_access(value, request)
            if owned_surface(method, path, mcp_path) == "mcp"
            else None
        )
    else:
        token = await store.bearer(value)
    if not token:
        return None, token
    surface, action = owned_surface(method, path, mcp_path), token_action(method, path)
    if not (
        path.startswith("/api/me/")
        or (surface is not None and token_covers(token, surface))
        or (action is not None and action in token["actions"])
    ):
        return None, None
    if not token.get("owner"):
        return {
            "id": token["id"],
            "email": "",
            "name": f"token:{token['name']}",
            "roles": [],
            "active": True,
        }, token
    async with store.locked() as d:
        user = next(
            (
                u.copy()
                for u in d["users"]
                if u["id"] == token["owner"] and u["active"] and not u["must_change_password"]
            ),
            None,
        )
    return user, token


def _anonymous_ok(request: Request, path: str, method: str, authz: str) -> bool:
    """Public status page (when on in Settings) and the read-only docs (HUB_PUBLIC_DOCS=0 needs login)."""
    if path == "/status" and method == "GET" and request.app.state.web.public_status() is not None:
        return True
    return (
        not authz
        and method in ("GET", "HEAD")
        and (
            path in ("/developers", "/llms.txt", "/dev/llms.txt")
            or path.startswith(("/developers/", "/dev/docs/"))
        )
        and os.getenv("HUB_PUBLIC_DOCS", "1") != "0"
    )


def _unauthenticated(request: Request, path: str, authz: str) -> Response:
    cfg = request.app.state.apicfg
    if path == cfg.mcp.path or path.startswith("/mcp/"):
        return cast(
            Response,
            request.app.state.oauth.unauthorized(
                request,
                "invalid_token" if authz else None,
                "Use OAuth ou um token pessoal com o escopo mcp.",
            ),
        )
    if path.startswith("/api/v1") or path == "/api/docs":
        return JSONResponse(
            {"type": "/api/v1/problems/unauthorized", "title": "Não autenticado", "status": 401,
             "detail": "Entre ou use um token de API.", "instance": path},
            status_code=401, media_type="application/problem+json",
            headers={"www-authenticate": 'Bearer realm="hub"'},
        )  # fmt: skip
    if authz or path.startswith(("/api/", "/_frag/")):
        return JSONResponse({"error": "Não autenticado."}, status_code=401)
    return RedirectResponse(
        f"/login?next={quote(path, safe='/')}"
        + (f"%3F{quote(request.url.query)}" if request.url.query else ""),
        status_code=303,
    )


def _cross_site(request: Request, token: dict[str, Any] | None) -> bool:
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")
    fetch_site = request.headers.get("sec-fetch-site")
    bearer_only = token is not None  # identity comes from the token (a cookie, if sent, is ignored)
    # `Sec-Fetch-Site: same-origin` is enough: with `Referrer-Policy: no-referrer` (OAuth consent)
    # the browser sends `Origin: null` on a POST from the hub itself.
    return bool(
        not bearer_only
        and fetch_site != "same-origin"
        and (
            fetch_site == "cross-site"
            or (not origin and fetch_site not in ("same-site", "none"))
            or (origin and origin.split("://", 1)[-1] != host)
        )
    )


def _set_principal(
    request: Request, user: dict[str, Any], token: dict[str, Any] | None
) -> tuple[list[str], list[Any] | None]:
    request.state.user, request.state.token = user, token
    prefs = request.app.state.ux.prefs(user["id"])
    request.state.lang = resolve(prefs.lang, request.headers.get("accept-language"))
    request.state.theme = prefs.theme
    perms, apps = permissions(user, request.app.state.roles)
    if apps is not None:  # access granted by a request approved in the Inbox
        apps = [*apps, *request.app.state.inbox.grants_for(user["id"])]
    request.state.permissions, request.state.apps = perms, apps
    return perms, apps


def _required(path: str, method: str, segs: list[str]) -> tuple[str, ...]:
    if path == "/" or segs[0] in ("applications", "apps", "_frag"):
        return ("acessar_aplicacoes",)
    if segs[0] == "admin":
        return admin_required(segs[1] if len(segs) > 1 else "")
    if segs[0] == "api":
        return () if len(segs) > 1 and segs[1] in ("me", "v1", "docs") else api_required(method, segs[1:])
    if segs[0] in ("logout", "account", "status", "inbox", "search", "oauth", "mcp"):
        return ()
    return ("acessar_aplicacoes",)


def _denied(
    path: str, method: str, token: dict[str, Any] | None, perms: list[str], apps: list[Any] | None
) -> bool:
    segs = path.strip("/").split("/")
    required = _required(path, method, segs)
    if token and token_action(method, path) in token.get("actions", []):
        required = ()  # CI token / `hub deploy` (upload/status): the token's action is the authorization
    in_app = segs[0] in ("apps", "_frag")
    app_id = segs[1] if in_app and len(segs) > 1 else segs[0]
    if required and not any(p in perms for p in required):
        return True
    if required == ("acessar_aplicacoes",) and in_app and apps is not None and app_id not in apps:
        return True
    return bool(
        token and token.get("apps") and in_app and app_id not in token["apps"]
    )  # token restrito a apps


def _forbidden(request: Request, path: str) -> Response:
    if path.startswith("/api/"):
        return JSONResponse({"error": "Acesso negado."}, status_code=403)
    w = request.app.state.web
    return cast(
        Response,
        w.templates.TemplateResponse(
            request,
            "forbidden.html",
            w.page_ctx(request)
            | {
                "message": "Você não tem permissão para acessar esta página.",
                "eyebrow": eyebrow_for(path, getattr(request.state, "lang", "pt-BR")),
            },
            status_code=403,
        ),
    )
