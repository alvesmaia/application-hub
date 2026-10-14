"""OAuth 2.1 for `/mcp` — Shared state, issuer/resource URLs, responses and the discovery metadata."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from hub.api.config import HubCfg
from hub.api.oauth_util import (
    _LOOPBACK,
    NO_ISSUER,
    SCOPES,
    WINDOW_MS,
    canon,
    esc,
    origin_of_url,
)
from hub.api.records import Records
from hub.api.v1 import take_rate
from hub.ux.texts import t


class OAuthBase:
    templates: Jinja2Templates

    inbox: Any

    def __init__(self, cfg: HubCfg, auth_store: Any, admin: Any, records: Records) -> None:
        self.hub, self.auth, self.admin, self.rec = cfg, auth_store, admin, records
        self.cimd_cache: dict[str, tuple[dict[str, Any], int]] = {}
        self.cimd_transport: Any = None  # testes
        self.resolver: Callable[[str, int], Awaitable[list[str]]] | None = None  # testes

    @property
    def cfg(self) -> Any:
        return self.hub.mcp.oauth

    @property
    def on(self) -> bool:
        return self.hub.oauth_on

    # ---- URLs --------------------------------------------------------------------------------------
    @staticmethod
    def public_url() -> str | None:
        import os

        return os.environ.get("HUB_PUBLIC_URL", "").strip().rstrip("/") or None

    @staticmethod
    def request_origin(request: Request) -> str | None:
        host = request.headers.get("host", "")
        if not host or not all(c.isalnum() or c in ".-:[]" for c in host):
            return None
        import os

        https = request.url.scheme == "https" or (
            os.getenv("HUB_TRUST_PROXY_HEADERS") == "1"
            and request.headers.get("x-forwarded-proto") == "https"
        )
        return f"{'https' if https else 'http'}://{host}"

    @staticmethod
    def issuer_from(public: str | None, origin: str | None) -> str | None:
        if public:
            return public
        if not origin:
            return None
        host = origin.split("://", 1)[-1]
        host = f"[{host[1:].split(']')[0]}]" if host.startswith("[") else host.split(":")[0].lower()
        return origin if host in _LOOPBACK else None

    def issuer(self, request: Request) -> str | None:
        return self.issuer_from(self.public_url(), self.request_origin(request))

    def resource(self, request: Request) -> str | None:
        i = self.issuer(request)
        return f"{i}{self.hub.mcp.path}" if i else None

    def origin_allowed(self, origin: str, request: Request) -> bool:
        """Origin of `/mcp` (anti DNS rebinding): the public URL's, `HUB_MCP_ALLOWED_ORIGINS`, or loopback."""
        import os

        o = canon(origin)
        if (pub := self.public_url()) and (po := origin_of_url(pub)) and canon(po) == o:
            return True
        if any(
            canon(x) == o
            for x in (s.strip() for s in os.getenv("HUB_MCP_ALLOWED_ORIGINS", "").split(","))
            if x
        ):
            return True
        if self.public_url() is None:
            i = self.issuer_from(None, self.request_origin(request))
            return bool(i and (canon(i) == o or canon(i.replace("http://", "https://", 1)) == o))
        return False

    def www_authenticate(self, request: Request, error: str | None = None) -> str:
        v = 'Bearer realm="hub"'
        if self.on:
            if iss := self.issuer(request):
                v += f', resource_metadata="{iss}/.well-known/oauth-protected-resource{self.hub.mcp.path}"'
            v += f', scope="{" ".join(self.cfg.allowed_scopes)}"'
        if error:
            v += f', error="{error}"'
        return v

    def unauthorized(self, request: Request, error: str | None, msg: str) -> Response:
        r = Response(json.dumps({"error": msg}, ensure_ascii=False), 401, media_type="application/json",
                     headers={"cache-control": "no-store"})  # fmt: skip
        r.headers["www-authenticate"] = self.www_authenticate(request, error)
        return r

    # ---- limite ------------------------------------------------------------------------------------
    async def hit(self, app: FastAPI, key: str, limit: int) -> bool:
        return await take_rate(app, "oauth", key, limit, WINDOW_MS) is None

    # ---- auditoria ---------------------------------------------------------------------------------
    async def audit(self, actor: str, title: str, detail: str) -> None:
        await self.admin.audit(actor, title, detail, icon="settings")

    async def user_label(self, uid: str) -> str:
        async with self.auth.locked() as d:
            u = next((x for x in d["users"] if x["id"] == uid), None)
        return str(u["email"] or u["name"]) if u else uid

    async def user_ok(self, uid: str) -> bool:
        async with self.auth.locked() as d:
            return any(u["id"] == uid and u["active"] and not u["must_change_password"] for u in d["users"])

    # ---- respostas ---------------------------------------------------------------------------------
    @staticmethod
    def json_res(status: int, v: Any, headers: dict[str, str] | None = None) -> Response:
        return Response(json.dumps(v, ensure_ascii=False), status, media_type="application/json",
                        headers={"cache-control": "no-store", "pragma": "no-cache", **(headers or {})})  # fmt: skip

    def oerr(self, status: int, code: str, desc: str, headers: dict[str, str] | None = None) -> Response:
        return self.json_res(status, {"error": code, "error_description": desc}, headers)

    @staticmethod
    def redirect(to: str) -> Response:
        return RedirectResponse(
            to, 303, headers={"cache-control": "no-store", "referrer-policy": "no-referrer"}
        )

    def card(
        self, request: Request, title: str, inner: str, eyebrow: str = "Autorizar aplicativo"
    ) -> dict[str, Any]:
        main = (f'<section class="auth-card oauth-consent" style="max-width:640px"><div class="eyebrow">{esc(eyebrow)}</div>'
                f'<h1 class="auth-title">{esc(title)}</h1>{inner}</section>')  # fmt: skip
        lang = getattr(request.state, "lang", "pt-BR")
        return {"title": title, "main": main, "lang": lang, "skip": t("shell.skip", lang),
                "home": t("shell.home_label", lang), "ws": t("shell.workspace", lang)}  # fmt: skip

    def page_res(
        self, request: Request, status: int, ctx: dict[str, Any], extra_origin: str | None = None
    ) -> Response:
        fa = f"'self' {extra_origin}" if extra_origin else "'self'"
        csp = ("default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self'; "
               f"form-action {fa}; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")  # fmt: skip
        r = self.templates.TemplateResponse(
            request, "oauth_page.html", ctx | {"brand": request.app.title}, status_code=status
        )
        r.headers.update({"content-security-policy": csp, "x-frame-options": "DENY", "referrer-policy": "no-referrer",
                          "cache-control": "no-store"})  # fmt: skip
        return r

    def error_page(self, request: Request, msg: str) -> Response:
        inner = (f'<p class="auth-copy" data-oauth-error="">{esc(msg)}</p><p class="auth-copy">Nada foi autorizado. Volte ao '
                 'aplicativo e tente conectar de novo; se persistir, fale com a administração da plataforma.</p>'
                 '<div class="auth-actions"><a class="secondary-button auth-link" href="/">Voltar ao início</a></div>')  # fmt: skip
        return self.page_res(request, 400, self.card(request, "Pedido de autorização inválido", inner))

    # ---- metadados ---------------------------------------------------------------------------------
    def no_issuer(self) -> Response:
        return self.oerr(404, "invalid_request", NO_ISSUER)

    def not_found(self) -> Response:
        return self.oerr(
            404,
            "not_found",
            "OAuth do MCP desligado nesta instalação ([mcp.oauth].enabled = false ou MCP desligado).",
        )

    def metadata_prm(self, request: Request) -> Response:
        iss, res = self.issuer(request), self.resource(request)
        if not iss or not res:
            return self.no_issuer()
        return self.json_res(200, {
            "resource": res, "authorization_servers": [iss], "scopes_supported": self.cfg.allowed_scopes,
            "bearer_methods_supported": ["header"], "resource_name": f"{request.app.title} (MCP)",
            "resource_documentation": f"{iss}/api/docs",
        }, {"cache-control": "max-age=300", "access-control-allow-origin": "*"})  # fmt: skip

    def metadata_as(self, request: Request) -> Response:
        iss, c = self.issuer(request), self.cfg
        if not iss:
            return self.no_issuer()
        methods = ["none", "client_secret_post", "client_secret_basic"]
        m: dict[str, Any] = {
            "issuer": iss, "authorization_endpoint": f"{iss}/oauth/authorize", "token_endpoint": f"{iss}/oauth/token",
            "revocation_endpoint": f"{iss}/oauth/revoke", "response_types_supported": ["code"],
            "response_modes_supported": ["query"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": methods,
            "revocation_endpoint_auth_methods_supported": methods, "scopes_supported": c.allowed_scopes,
            "authorization_response_iss_parameter_supported": True,
            "client_id_metadata_document_supported": c.client_metadata_documents, "service_documentation": f"{iss}/api/docs",
        }  # fmt: skip
        if c.dynamic_registration:
            m["registration_endpoint"] = f"{iss}/oauth/register"
        return self.json_res(200, m, {"cache-control": "max-age=300", "access-control-allow-origin": "*"})

    def scopes_of(self, s: str) -> list[str]:
        if not s.strip():
            return list(self.cfg.allowed_scopes)
        out: list[str] = []
        for x in s.replace(",", " ").split():
            if x in SCOPES and x in self.cfg.allowed_scopes and x not in out:
                out.append(x)
        return out if "mcp" in out else ["mcp", *out]
