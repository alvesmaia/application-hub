"""The hub as OAuth 2.1 authorization server and resource server for `/mcp` (RFC 8414, 9728, 7591, 7009,
9207, 8707; PKCE S256 obrigatório).

State in `records` (state database or memory), key = SHA-256 of the secret: only hashes are stored. The access token
(`hubo_…`) is bound to the `resource` (this hub's `/mcp`) and is valid only there; it acts as the owner (same RBAC as the UI);
scopes only restrict (`mcp` = read; `api:write` = tools that change something)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from hub.api.oauth_tokens import OAuthTokens
from hub.api.oauth_util import (
    K_CLIENT,
    K_GRANT,
    KEPT_CLIENT_MS,
    form,
    host_of,
    now_ms,
    sha,
    short,
)
from hub.api.v1 import principal
from hub.state import Change
from hub.ux.texts import ago


class OAuth(OAuthTokens):
    """The whole server: base → clients → authorize → tokens → admin views (one class per module)."""

    # ---- telas -------------------------------------------------------------------------------------
    async def grants(self, user: str | None = None) -> list[dict[str, Any]]:
        v = [x[1] for x in await self.rec.list(K_GRANT, ("user", user) if user else None)]
        return sorted(v, key=lambda g: -g.get("created_at", 0))

    async def clients(self) -> list[dict[str, Any]]:
        out = [
            {k: x for k, x in v.items() if k != "secret_hash"} for _, v, _ in await self.rec.list(K_CLIENT)
        ]
        return sorted(out, key=lambda c: -c.get("created_at", 0))

    async def grants_view(self, uid: str, lang: str = "pt-BR") -> list[dict[str, Any]]:
        now, rows = now_ms(), []
        for g in await self.grants(uid):
            redirect = g.get("redirect_uri", "")
            host = host_of(redirect) if redirect else host_of(g.get("client_id", ""))
            rows.append({"id": g["grant_id"], "name": short(g.get("client_name", "")),
                         "unverified": g.get("client_kind") != "verified", "host": host or "—",
                         "scopes": " ".join(g.get("scopes", [])), "created": ago(g.get("created_at", 0), now, lang),
                         "last": ago(g.get("last_used", 0), now, lang)})  # fmt: skip
        return rows

    async def revoke_for_user(self, uid: str, gid: str, actor: str = "") -> bool:
        g = await self.rec.get(K_GRANT, gid)
        if not g or g.get("user") != uid:
            return False
        if g := await self.revoke_grant(gid):
            await self.audit(actor or await self.user_label(uid), "OAuth: acesso de aplicativo revogado pelo usuário",
                             f"{g.get('client_name', '')} · {g.get('client_id', '')}")  # fmt: skip
        return True

    async def admin_action(self, path: str, body: bytes, actor: str) -> Response:  # noqa: C901
        f = form(body.decode("utf-8", "replace"))
        match path:
            case "/admin/oauth/revoke-grant":
                if g := await self.revoke_grant(f.get("grant", "")):
                    await self.audit(actor, "OAuth: concessão revogada pela administração",
                                     f"{g.get('client_name', '')} · usuário {await self.user_label(g.get('user', ''))}")  # fmt: skip
            case "/admin/oauth/block-client" | "/admin/oauth/unblock-client":
                cid, block = f.get("client", ""), path.endswith("/block-client")

                def upd(cur: Any) -> tuple[Change, None]:
                    v = cur or {
                        "client_id": cid,
                        "client_name": cid,
                        "redirect_uris": [],
                        "kind": "cimd",
                        "created_at": now_ms(),
                    }
                    return Change.set(v | {"disabled": block}, now_ms() + KEPT_CLIENT_MS), None

                await self.rec.update(K_CLIENT, sha(cid), upd)
                if block:
                    for g in await self.grants():
                        if g.get("client_id") == cid:
                            await self.revoke_grant(g["grant_id"])
                    self.cimd_cache.pop(cid, None)
                await self.audit(
                    actor,
                    "OAuth: cliente bloqueado (concessões revogadas)"
                    if block
                    else "OAuth: cliente desbloqueado",
                    cid,
                )
            case "/admin/oauth/remove-client":
                cid = f.get("client", "")
                for g in await self.grants():
                    if g.get("client_id") == cid:
                        await self.revoke_grant(g["grant_id"])
                await self.rec.delete(K_CLIENT, sha(cid))
                await self.audit(actor, "OAuth: cliente removido", cid)
            case _:
                return self.oerr(404, "not_found", "Ação desconhecida.")
        return self.redirect("/admin/tokens#oauth")


def mount_oauth(app: FastAPI, templates: Jinja2Templates) -> None:  # noqa: C901
    oa = cast(OAuth, app.state.oauth)
    oa.templates, oa.inbox = templates, app.state.inbox
    wk = "/.well-known/oauth-protected-resource"

    @app.api_route(
        "/.well-known/oauth-authorization-server",
        methods=["GET", "HEAD", "POST", "PUT", "DELETE"],
        include_in_schema=False,
    )
    async def as_meta(request: Request) -> Response:
        if not oa.on:
            return oa.not_found()
        if request.method not in ("GET", "HEAD"):
            return oa.oerr(405, "invalid_request", "Use GET.")
        return oa.metadata_as(request)

    @app.api_route(wk, methods=["GET", "HEAD", "POST", "PUT", "DELETE"], include_in_schema=False)
    @app.api_route(
        wk + "/{rest:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE"], include_in_schema=False
    )
    async def prm_meta(request: Request, rest: str = "") -> Response:
        if not oa.on:
            return oa.not_found()
        if request.method not in ("GET", "HEAD"):
            return oa.oerr(405, "invalid_request", "Use GET.")
        if rest and "/" + rest != oa.hub.mcp.path:
            return oa.oerr(404, "not_found", "Recurso desconhecido.")
        return oa.metadata_prm(request)

    async def small(request: Request) -> bytes | Response:
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 64 * 1024:
                return oa.oerr(413, "invalid_request", "Corpo grande demais.")
        return body

    def machine(fn: Callable[[Request, bytes], Any]) -> Callable[[Request], Any]:
        async def h(request: Request) -> Response:
            if not oa.on:
                return oa.not_found()
            body = await small(request)
            return body if isinstance(body, Response) else cast(Response, await fn(request, body))

        return h

    app.add_api_route("/oauth/register", machine(oa.register), methods=["POST"], include_in_schema=False)
    app.add_api_route("/oauth/token", machine(oa.token), methods=["POST"], include_in_schema=False)
    app.add_api_route("/oauth/revoke", machine(oa.revoke), methods=["POST"], include_in_schema=False)

    @app.get("/oauth/authorize", include_in_schema=False)
    async def authorize_get(request: Request) -> Response:
        return await oa.authorize_get(request) if oa.on else oa.not_found()

    @app.post("/oauth/authorize", include_in_schema=False)
    async def authorize_post(request: Request) -> Response:
        if not oa.on:
            return oa.not_found()
        body = await small(request)
        return body if isinstance(body, Response) else await oa.authorize_post(request, body)

    @app.post("/admin/oauth/{action}", include_in_schema=False)
    async def admin_action(request: Request, action: str) -> Response:
        p = principal(request)
        body = await small(request)
        if p is None or isinstance(body, Response):
            return oa.oerr(401, "unauthorized", "Entre no hub.")
        return await oa.admin_action(f"/admin/oauth/{action}", body, p.name)

    _ = urlsplit
