"""The hub as OAuth 2.1 authorization server and resource server for `/mcp` (RFC 8414, 9728, 7591, 7009,
9207, 8707; PKCE S256 obrigatório).

State in `records` (state database or memory), key = SHA-256 of the secret: only hashes are stored. The access token
(`hubo_…`) is bound to the `resource` (this hub's `/mcp`) and is valid only there; it acts as the owner (same RBAC as the UI);
scopes only restrict (`mcp` = read; `api:write` = tools that change something)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import secrets
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from urllib.parse import parse_qsl, quote, urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from hub.api.config import HubCfg
from hub.api.records import Records
from hub.api.v1 import principal, take_rate
from hub.auth import client_ip
from hub.state import Change
from hub.ux.texts import ago, t

SCOPES = ("mcp", "api:write")
K_CLIENT, K_CODE, K_GRANT, K_REFRESH, K_ACCESS, K_CONSENT = (
    "oauth.client", "oauth.code", "oauth.grant", "oauth.refresh", "oauth.access", "oauth.consent",
)  # fmt: skip
DAY_MS = 86_400_000
UNUSED_CLIENT_MS, KEPT_CLIENT_MS = 3_600_000, 3650 * DAY_MS
MAX_UNUSED_CLIENTS, PENDING_CONSENT_RESERVE = 2000, 100
REGISTER_GLOBAL, REGISTER_PER_IP, REGISTER_GLOBAL_WEIGHT = 600, 20, 5
CONSENT_PER_USER, REVOKE_PER_IP, TOKEN_PER_IP = 60, 300, 300
CONSENT_TTL_MS = 10 * 60_000
WINDOW_MS = 15 * 60_000
NO_ISSUER = (
    "OAuth indisponível neste endereço: defina HUB_PUBLIC_URL com o endereço público do hub (sem ela, só "
    "localhost/127.0.0.1 respondem)."
)
_LOOPBACK = ("localhost", "127.0.0.1", "[::1]")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#39;"))  # fmt: skip


def enc(s: str) -> str:
    return quote(s, safe="-_.~")


def with_query(base: str, pairs: list[tuple[str, str]]) -> str:
    sep = "&" if "?" in base else "?"
    return base + sep + "&".join(f"{k}={enc(v)}" for k, v in pairs)


def canon(u: str) -> str:
    u = u.strip()
    if "://" not in u:
        return u.rstrip("/")
    scheme, rest = u.split("://", 1)
    cut = next((i for i, c in enumerate(rest) if c in "/?#"), len(rest))
    return f"{scheme.lower()}://{rest[:cut].lower()}{rest[cut:]}".rstrip("/")


def parse_url(u: str) -> tuple[str, str, str | None, str] | None:
    """(scheme, host, port, path+query) or None (no credentials, fragment, spaces or control characters)."""
    if "://" not in u or "#" in u or any(c.isspace() or not c.isprintable() for c in u):
        return None
    scheme, rest = u.split("://", 1)
    cut = next((i for i, c in enumerate(rest) if c in "/?"), len(rest))
    auth, pq = rest[:cut], rest[cut:]
    if not auth or "@" in auth:
        return None
    if auth.startswith("["):
        host, _, tail = auth[1:].partition("]")
        host, port = f"[{host}]", (tail[1:] if tail.startswith(":") else None)
    else:
        host, _, port_s = auth.partition(":")
        port = port_s if ":" in auth else None
    if port is not None and not port.isdigit():
        return None
    return scheme.lower(), host.lower(), port, pq


def host_of(u: str) -> str:
    p = parse_url(u)
    return "" if p is None else f"{p[1]}:{p[2]}" if p[2] else p[1]


def origin_of_url(u: str) -> str | None:
    p = parse_url(u)
    return None if p is None else f"{p[0]}://{p[1]}" + (f":{p[2]}" if p[2] else "")


def redirect_ok(u: str, allowed_hosts: list[str]) -> bool:
    """Acceptable `redirect_uri`: HTTPS (allowed host) or loopback HTTP."""
    p = parse_url(u) if len(u) <= 2000 else None
    if p is None:
        return False
    if p[0] == "http":
        return p[1] in _LOOPBACK
    return p[0] == "https" and (
        p[1] in _LOOPBACK or not allowed_hosts or p[1] in [h.lower() for h in allowed_hosts]
    )


def redirect_matches(registered: str, presented: str) -> bool:
    """Exact match; on loopback the port may vary (RFC 8252 §7.3)."""
    if registered == presented:
        return True
    a, b = parse_url(registered), parse_url(presented)
    return bool(a and b and a[0] == b[0] == "http" and a[1] in _LOOPBACK and a[1] == b[1] and a[3] == b[3]
                and b[2] is not None)  # fmt: skip


def form(s: str) -> dict[str, str]:
    """urlencoded fields; a repeated parameter is an error in OAuth: keeps the first and marks `<k>\\0dup`."""
    m: dict[str, str] = {}
    for k, v in parse_qsl(s, keep_blank_values=True):
        if k in m:
            m[f"{k}\0dup"] = ""
        else:
            m[k] = v
    return m


def _invisible(c: str) -> bool:
    o = ord(c)
    return (not c.isprintable() and not c.isspace()) or o in (0xAD, 0x61C, 0x180E, 0xFEFF) or 0x200B <= o <= 0x200F or 0x2028 <= o <= 0x202E or 0x2060 <= o <= 0x2064 or 0x2066 <= o <= 0x206F or 0xFFF9 <= o <= 0xFFFB or 0xE0000 <= o <= 0xE007F  # fmt: skip


def clean_name(s: str) -> str:
    return "".join(c for c in s if (not _invisible(c) and not c.isspace()) or c == " ")[:80].strip()


def confusable(s: str) -> bool:
    latin = any(c.isascii() and c.isalpha() for c in s)
    other = any(c.isalpha() and not c.isascii() and not 0xC0 <= ord(c) <= 0x24F for c in s)
    return latin and other


def short(s: str) -> str:
    return s[:47] + "…" if len(s) > 48 else s


def pkce_ok(verifier: str, challenge: str) -> bool:
    if not 43 <= len(verifier) <= 128 or not all(
        c.isascii() and (c.isalnum() or c in "-._~") for c in verifier
    ):
        return False
    calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.compare_digest(calc, challenge)


def scope_label(s: str) -> tuple[str, str]:
    return {
        "mcp": ("Usar as ferramentas do hub como você",
                "Ler catálogo, status, implantações, logs e as ferramentas de leitura dos apps que você acessa — com as suas permissões, nunca mais."),
        "api:write": ("Executar ações que alteram algo",
                      "Implantar, reverter, fazer backup, cancelar tarefas e as ações dos apps. Ações destrutivas continuam exigindo a sua aprovação na Inbox, com senha ou login novo."),
    }.get(s, ("", ""))  # fmt: skip


def ip_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return ip_public(ip.ipv4_mapped)
        b = ip.packed
        if b[:12] == bytes(12):
            return ip_public(ipaddress.IPv4Address(b[12:]))
        if b[:12] == bytes.fromhex("0064ff9b0000000000000000"):
            return ip_public(ipaddress.IPv4Address(b[12:]))
        if b[:2] == b"\x20\x02":
            return ip_public(ipaddress.IPv4Address(b[2:6]))
        s = int(ip) >> 112
        return not (ip.is_loopback or ip.is_unspecified or ip.is_multicast or (s & 0xFE00) == 0xFC00
                    or (s & 0xFFC0) in (0xFE80, 0xFEC0) or b[:4] in (b"\x20\x01\x00\x00", b"\x20\x01\x0d\xb8")
                    or (b[:2] == b"\x00\x64" and b[2:4] == b"\xff\x9b"))  # fmt: skip
    o = ip.packed
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or ip.is_reserved or o[0] == 0 or (o[0] == 100 and o[1] & 0xC0 == 64) or (o[0] == 198 and o[1] & 0xFE == 18)
                or o[0] >= 224)  # fmt: skip


class OAuthError(Exception):
    pass


class OAuth:
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

    # ---- clientes ----------------------------------------------------------------------------------
    @staticmethod
    def client_from(v: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(v.get("client_id"), str) or not isinstance(v.get("redirect_uris"), list):
            return None
        return {"id": v["client_id"], "name": v.get("client_name", ""), "redirect_uris": v["redirect_uris"],
                "kind": v.get("kind", "dcr"), "auth_method": v.get("token_endpoint_auth_method", "none"),
                "secret_hash": v.get("secret_hash"), "disabled": bool(v.get("disabled"))}  # fmt: skip

    async def fetch_cimd(self, url: str) -> dict[str, Any]:
        now = now_ms()
        if (c := self.cimd_cache.get(url)) and c[1] > now:
            return c[0]
        import os

        private_ok = os.getenv("HUB_OAUTH_CIMD_ALLOW_PRIVATE", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
            "sim",
        )
        p = parse_url(url)
        if p is None:
            raise OAuthError("client_id não é uma URL válida")
        if p[0] != "https" and not (private_ok and p[0] == "http"):
            raise OAuthError("client_id (URL) precisa ser HTTPS")
        if p[3].split("?")[0] in ("", "/"):
            raise OAuthError("client_id (URL) precisa ter caminho")
        host = p[1].strip("[]")
        port = int(p[2]) if p[2] else (443 if p[0] == "https" else 80)
        try:
            if self.resolver:
                addrs = await self.resolver(host, port)
            else:
                infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
                addrs = [str(i[4][0]) for i in infos]
        except OSError as e:
            raise OAuthError(f"host do client_id não resolve: {e}") from e
        ips = [ipaddress.ip_address(a) for a in addrs]
        if not ips:
            raise OAuthError("host do client_id não resolve")
        if not private_ok and any(not ip_public(ip) for ip in ips):
            raise OAuthError("client_id aponta para endereço interno (recusado)")
        pin = f"[{ips[0]}]" if ips[0].version == 6 else str(ips[0])
        target = url.replace(p[1] + (f":{p[2]}" if p[2] else ""), f"{pin}:{port}", 1)
        body = b""
        try:
            async with httpx.AsyncClient(
                timeout=5, follow_redirects=False, transport=self.cimd_transport
            ) as cli, cli.stream("GET", target, headers={"host": host, "accept": "application/json"},
                                  extensions={"sni_hostname": host}) as res:  # fmt: skip
                if res.status_code >= 300:
                    raise OAuthError(f"documento do cliente respondeu {res.status_code}")
                async for chunk in res.aiter_bytes():
                    body += chunk
                    if len(body) > 16 * 1024:
                        raise OAuthError("documento do cliente grande demais")
        except httpx.HTTPError as e:
            raise OAuthError(f"documento do cliente inacessível: {e}") from e
        try:
            v = json.loads(body)
        except ValueError as e:
            raise OAuthError("documento do cliente não é JSON válido") from e
        if not isinstance(v, dict) or v.get("client_id") != url:
            raise OAuthError("client_id do documento não é a própria URL")
        if not str(v.get("client_name", "")).strip():
            raise OAuthError("documento do cliente sem client_name")
        uris = v.get("redirect_uris")
        if not isinstance(uris, list):
            raise OAuthError("documento do cliente sem redirect_uris")
        if not uris or any(
            not isinstance(u, str) or not redirect_ok(u, self.cfg.allowed_redirect_hosts) for u in uris
        ):
            raise OAuthError("redirect_uris do documento inválidas ou não permitidas")
        if v.get("token_endpoint_auth_method", "none") != "none":
            raise OAuthError("documento do cliente: só token_endpoint_auth_method = none")
        c = {"id": url, "name": clean_name(v["client_name"]), "redirect_uris": uris, "kind": "cimd",
             "auth_method": "none", "secret_hash": None, "disabled": False}  # fmt: skip
        if len(self.cimd_cache) > 500:
            self.cimd_cache = {k: x for k, x in self.cimd_cache.items() if x[1] > now}
        self.cimd_cache[url] = (c, now + 5 * 60_000)
        return c

    async def load_client(self, cid: str) -> dict[str, Any]:
        raw = await self.rec.get(K_CLIENT, sha(cid))
        stored = self.client_from(raw) if raw else None
        if stored and stored["disabled"]:
            raise OAuthError("cliente bloqueado pela administração da plataforma")
        if cid.startswith(("https://", "http://")):
            if not self.cfg.client_metadata_documents:
                raise OAuthError("client_id por URL (metadata document) desligado neste hub")
            return await self.fetch_cimd(cid)
        if stored is None:
            raise OAuthError("client_id desconhecido")
        return stored

    async def remember_cimd(self, c: dict[str, Any]) -> None:
        fresh = {"client_id": c["id"], "client_name": c["name"], "redirect_uris": c["redirect_uris"], "kind": "cimd",
                 "token_endpoint_auth_method": "none", "created_at": now_ms(), "used": True}  # fmt: skip

        def f(cur: Any) -> tuple[Change, None]:
            if cur and cur.get("disabled"):
                return Change.keep(), None
            if cur:
                cur = cur | {
                    "client_name": fresh["client_name"],
                    "redirect_uris": fresh["redirect_uris"],
                    "used": True,
                }
                return Change.set(cur, now_ms() + KEPT_CLIENT_MS), None
            return Change.set(fresh, now_ms() + KEPT_CLIENT_MS), None

        await self.rec.update(K_CLIENT, sha(c["id"]), f)

    async def evict_unused(self, need: int) -> None:
        now = now_ms()
        cands = sorted(
            await self.rec.list(K_CLIENT, ("used", "false"), 20_000), key=lambda x: x[1].get("created_at", 0)
        )
        pending = sum(1 for _, v, _ in cands if v.get("consent_until", 0) > now)
        unprotected, done = max(pending - PENDING_CONSENT_RESERVE, 0), 0
        for key, v, _ in cands:
            if done >= need:
                break
            is_pending = v.get("consent_until", 0) > now
            force = is_pending and unprotected > 0
            if is_pending and not force:
                continue
            unprotected -= 1 if force else 0

            def f(cur: Any, force: bool = force) -> tuple[Change, bool]:
                if (
                    cur
                    and cur.get("used") is not True
                    and cur.get("disabled") is not True
                    and (force or cur.get("consent_until", 0) <= now)
                ):
                    return Change.delete(), True
                return Change.keep(), False

            if await self.rec.update(K_CLIENT, key, f):
                done += 1

    async def mark_consent_pending(self, cid: str, until: int) -> None:
        def f(cur: Any) -> tuple[Change, None]:
            if cur and cur.get("used") is not True:
                return Change.set(
                    cur | {"consent_until": until}, now_ms() + max(UNUSED_CLIENT_MS, until - now_ms())
                ), None
            return Change.keep(), None

        await self.rec.update(K_CLIENT, sha(cid), f)

    async def mark_client_used(self, cid: str) -> None:
        def f(cur: Any) -> tuple[Change, None]:
            if cur and cur.get("used") is not True:
                return Change.set(cur | {"used": True}, now_ms() + KEPT_CLIENT_MS), None
            return Change.keep(), None

        await self.rec.update(K_CLIENT, sha(cid), f)

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

    # ---- registro (RFC 7591) -----------------------------------------------------------------------
    async def register(self, request: Request, body: bytes) -> Response:
        c, app = self.cfg, request.app
        if not c.dynamic_registration:
            return self.oerr(
                404,
                "not_found",
                "Registro dinâmico de clientes desligado ([mcp.oauth].dynamic_registration = false).",
            )
        peer = client_ip(request)
        over = not await self.hit(app, f"oauth-reg:{peer}", REGISTER_PER_IP)
        if not over:
            reserved = await self.hit(app, f"oauth-reg-g:{peer}", REGISTER_GLOBAL_WEIGHT)
            glob = await self.hit(app, "oauth-reg:*", REGISTER_GLOBAL)
            over = not (glob or reserved)
        if over:
            return self.oerr(429, "slow_down", "Registros demais deste endereço; tente mais tarde.")

        def bad(d: str) -> Response:
            return self.oerr(400, "invalid_client_metadata", d)

        try:
            v = json.loads(body)
        except ValueError:
            return bad("Corpo JSON inválido.")
        if not isinstance(v, dict):
            return bad("Corpo JSON inválido.")
        raw = v.get("redirect_uris")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 10:
            return self.oerr(400, "invalid_redirect_uri", "redirect_uris obrigatório (1 a 10).")
        uris = [u for u in raw if isinstance(u, str)]
        for u in uris:
            if not redirect_ok(u, c.allowed_redirect_hosts):
                return self.oerr(
                    400,
                    "invalid_redirect_uri",
                    f"redirect_uri não permitida: {u} (use https, ou http só em localhost; sem fragmento; hosts permitidos pela administração)",
                )
        method = v.get("token_endpoint_auth_method", "client_secret_basic")
        if method not in ("none", "client_secret_post", "client_secret_basic"):
            return bad("token_endpoint_auth_method: none, client_secret_post ou client_secret_basic.")
        if isinstance(gt := v.get("grant_types"), list) and any(
            g not in ("authorization_code", "refresh_token") for g in gt
        ):
            return bad("grant_types: authorization_code e refresh_token.")
        if isinstance(rt := v.get("response_types"), list) and any(g != "code" for g in rt):
            return bad("response_types: code.")
        name = clean_name(str(v.get("client_name") or "")) or "Cliente sem nome"
        unused = await self.rec.count(K_CLIENT, ("used", "false"))
        if unused >= MAX_UNUSED_CLIENTS:
            await self.evict_unused(unused - MAX_UNUSED_CLIENTS + 1)
        now, cid = now_ms(), f"hubc_{secrets.token_hex(16)}"
        secret = f"hubcs_{secrets.token_hex(24)}" if method != "none" else None
        rec = {"client_id": cid, "client_name": name, "redirect_uris": uris, "kind": "dcr",
               "token_endpoint_auth_method": method, "secret_hash": sha(secret) if secret else None,
               "created_at": now, "used": False, "ip": peer}  # fmt: skip
        await self.rec.put(K_CLIENT, sha(cid), rec, now + UNUSED_CLIENT_MS)
        await self.audit("OAuth", "OAuth: cliente registrado (registro dinâmico)",
                         f"{name} · {cid} · retorno: {', '.join(host_of(u) for u in uris)}")  # fmt: skip
        out: dict[str, Any] = {
            "client_id": cid, "client_id_issued_at": now // 1000, "client_name": name, "redirect_uris": uris,
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": method,
        }  # fmt: skip
        if secret:
            out |= {"client_secret": secret, "client_secret_expires_at": 0}
        return self.json_res(201, out)

    # ---- authorization ----------------------------------------------------------------------------
    def back_error(self, redirect_uri: str, iss: str, state: str | None, code: str, desc: str) -> Response:
        q = [("error", code), ("error_description", desc), ("iss", iss)]
        if state is not None:
            q.append(("state", state))
        return self.redirect(with_query(redirect_uri, q))

    async def authorize_get(self, request: Request) -> Response:
        q = form(request.url.query)
        if any(k.endswith("\0dup") for k in q):
            return self.error_page(request, "Parâmetro repetido no pedido de autorização.")
        if not (iss := self.issuer(request)):
            return self.error_page(request, NO_ISSUER)
        try:
            client = await self.load_client(q.get("client_id", ""))
        except OAuthError as e:
            return self.error_page(request, f"Cliente inválido: {e}.")
        uris = client["redirect_uris"]
        redirect_uri = q.get("redirect_uri") or (uris[0] if len(uris) == 1 else None)
        if redirect_uri is None:
            return self.error_page(request, "redirect_uri obrigatória.")
        if not any(redirect_matches(r, redirect_uri) for r in uris):
            return self.error_page(request, "redirect_uri não registrada para este cliente.")
        state = q.get("state")

        def back(code: str, desc: str) -> Response:
            return self.back_error(redirect_uri, iss, state, code, desc)

        if q.get("response_type") != "code":
            return back("unsupported_response_type", "Use response_type=code.")
        challenge = q.get("code_challenge", "")
        if q.get("code_challenge_method") != "S256" or not 43 <= len(challenge) <= 128:
            return back(
                "invalid_request", "PKCE obrigatório: code_challenge (S256) e code_challenge_method=S256."
            )
        if not (ours := self.resource(request)):
            return self.error_page(request, NO_ISSUER)
        if (r := q.get("resource")) is not None and canon(r) != canon(ours):
            return back("invalid_target", f"resource deve ser {ours} (o servidor MCP deste hub).")
        p = principal(request)
        if p is None or not p.session:
            return self.error_page(request, "Entre no hub com a sua conta para autorizar um aplicativo.")
        if not await self.hit(request.app, f"oauth-consent:{p.uid}", CONSENT_PER_USER):
            return self.error_page(
                request, "Pedidos de autorização demais em pouco tempo; tente de novo mais tarde."
            )
        req = {"client_id": client["id"], "client_name": client["name"], "client_kind": client["kind"],
               "redirect_uri": redirect_uri, "challenge": challenge, "scopes": self.scopes_of(q.get("scope", "")),
               "resource": ours, "state": state, "user": p.uid}  # fmt: skip
        nonce = secrets.token_hex(24)
        await self.mark_consent_pending(client["id"], now_ms() + CONSENT_TTL_MS)
        await self.rec.put(K_CONSENT, sha(nonce), req, now_ms() + CONSENT_TTL_MS)
        who = p.user.get("email") or p.name
        inner = self.consent_inner(request.app.title, who, req, nonce)
        return self.page_res(
            request,
            200,
            self.card(request, "Conectar um aplicativo ao hub", inner),
            origin_of_url(redirect_uri),
        )

    def consent_inner(self, brand: str, who: str, r: dict[str, Any], nonce: str) -> str:
        kind = (f"Identificado pelo documento em <code>{esc(host_of(r['client_id']))}</code>" if r["client_kind"] == "cimd"
                else "Registrado automaticamente (registro dinâmico) — o nome é declarado pelo próprio aplicativo")  # fmt: skip
        scopes = ""
        for s in r["scopes"]:
            ttl, desc = scope_label(s)
            control = ('<input type="checkbox" checked disabled/><input type="hidden" name="scope" value="mcp"/>' if s == "mcp"
                       else f'<input type="checkbox" name="scope" value="{esc(s)}" checked/>')  # fmt: skip
            scopes += (f'<li><label class="oauth-scope">{control} <span><strong>{esc(ttl)}</strong> <code>{esc(s)}</code><br/>'
                       f'<small class="ops-muted">{esc(desc)}</small></span></label></li>')  # fmt: skip
        warn = ('<strong data-oauth-confusable="">Atenção: o nome mistura letras de alfabetos diferentes (pode imitar outro aplicativo).</strong> Confira o endereço de retorno.'
                if confusable(r["client_name"])
                else "O hub não verifica quem é este aplicativo: confira o endereço de retorno antes de autorizar.")  # fmt: skip
        host, name = esc(host_of(r["redirect_uri"])), esc(r["client_name"])
        return (
            f'<p class="auth-copy"><span class="status-pill status-maintenance" data-oauth-unverified="">Não verificado</span> {warn}</p>'
            f'<p class="auth-copy">O aplicativo em <strong>{host}</strong> (que se apresenta como <strong data-oauth-client="">{name}</strong>) quer acessar o <strong>{esc(brand)}</strong> em seu nome (<strong>{esc(who)}</strong>).</p>\n'
            f'<dl class="oauth-facts">\n<dt>Aplicativo</dt><dd>{name} <small class="ops-muted">(nome declarado pelo aplicativo — confira o endereço abaixo)</small><br/><small class="ops-muted">{kind}</small></dd>\n'
            f'<dt>Volta para</dt><dd><code data-oauth-redirect-host="">{host}</code></dd>\n<dt>Acesso a</dt><dd><code>{esc(r["resource"])}</code></dd>\n</dl>\n'
            f'<form method="post" action="/oauth/authorize">\n<input type="hidden" name="consent" value="{nonce}"/>\n'
            f'<p class="auth-copy"><strong>Permissões pedidas</strong></p>\n<ul class="oauth-scopes" style="list-style:none;padding:0">{scopes}</ul>\n'
            f'<p class="ops-muted auth-note">O acesso vale até você revogar em <a href="/account/access#apps">Conta › Apps autorizados</a> (ou por {self.cfg.refresh_token_ttl_days} dias sem uso). Só autorize aplicativos em que você confia.</p>\n'
            '<div class="auth-actions"><button type="submit" name="decision" value="approve" class="primary-button" data-oauth-approve="">Autorizar</button> '
            '<button type="submit" name="decision" value="deny" class="secondary-button" data-oauth-deny="">Recusar</button></div>\n</form>'
        )  # fmt: skip

    async def authorize_post(self, request: Request, body: bytes) -> Response:
        text = body.decode("utf-8", "replace")
        f = form(text)
        if not (iss := self.issuer(request)):
            return self.error_page(request, NO_ISSUER)
        p = principal(request)
        if p is None or not p.session:
            return self.error_page(request, "Entre no hub com a sua conta para autorizar um aplicativo.")
        req = await self.rec.take(K_CONSENT, sha(f.get("consent", "")))
        if not req or req.get("user") != p.uid:
            return self.error_page(
                request,
                "Esta tela de autorização venceu ou já foi usada. Volte ao aplicativo e conecte de novo.",
            )
        try:
            client = await self.load_client(req["client_id"])
        except OAuthError as e:
            return self.error_page(request, f"Cliente inválido: {e}.")
        state = req.get("state")
        if f.get("decision") != "approve":
            await self.audit(
                p.name, "OAuth: autorização recusada", f"{req['client_name']} · {req['client_id']}"
            )
            return self.back_error(
                req["redirect_uri"], iss, state, "access_denied", "O usuário recusou o acesso."
            )
        chosen = [v for k, v in parse_qsl(text, keep_blank_values=True) if k == "scope"]
        scopes = [s for s in req["scopes"] if s == "mcp" or s in chosen]
        code, now = secrets.token_hex(32), now_ms()
        await self.rec.put(K_CODE, sha(code), {
            "client_id": req["client_id"], "client_name": req["client_name"], "user": p.uid,
            "redirect_uri": req["redirect_uri"], "challenge": req["challenge"], "scopes": scopes,
            "resource": req["resource"]}, now + self.cfg.code_ttl_s * 1000)  # fmt: skip
        if req["client_kind"] == "cimd":
            await self.remember_cimd(client)
        else:
            await self.mark_client_used(req["client_id"])
        await self.audit(p.name, "OAuth: acesso autorizado a aplicativo",
                         f"{req['client_name']} · {req['client_id']} · escopos: {' '.join(scopes)} · retorno: {host_of(req['redirect_uri'])}")  # fmt: skip
        q = [("code", code), ("iss", iss)]
        if state is not None:
            q.append(("state", state))
        return self.redirect(with_query(req["redirect_uri"], q))

    # ---- token -------------------------------------------------------------------------------------
    def client_auth(self, request: Request, f: dict[str, str]) -> tuple[str, str | None] | Response:
        def invalid() -> Response:
            return self.oerr(
                401,
                "invalid_client",
                "Autenticação do cliente inválida.",
                {"www-authenticate": 'Basic realm="hub-oauth"'},
            )

        if a := request.headers.get("authorization"):
            if not a.lower().startswith("basic "):
                return invalid()
            try:
                s = base64.b64decode(a[6:].strip(), validate=True).decode()
            except ValueError:
                return invalid()
            cid, sep, sec = s.partition(":")
            if not sep:
                return invalid()
            return _unq(cid), _unq(sec)
        if "client_id" not in f:
            return invalid()
        return f["client_id"], f.get("client_secret")

    async def authenticate_client(self, request: Request, f: dict[str, str]) -> dict[str, Any] | Response:
        ca = self.client_auth(request, f)
        if isinstance(ca, Response):
            return ca
        cid, secret = ca
        try:
            c = await self.load_client(cid)
        except OAuthError as e:
            return self.oerr(401, "invalid_client", str(e))
        if c["secret_hash"] is None:
            if c["auth_method"] == "none":
                return c
            return self.oerr(401, "invalid_client", "Cliente confidencial sem segredo registrado.")
        if secret is not None and secrets.compare_digest(c["secret_hash"], sha(secret)):
            return c
        return self.oerr(401, "invalid_client", "client_secret ausente ou inválido.")

    async def issue(self, grant: dict[str, Any]) -> dict[str, Any]:
        c, now = self.cfg, now_ms()
        access, refresh = f"hubo_{secrets.token_hex(32)}", f"hubr_{secrets.token_hex(32)}"
        gid, exp = grant["grant_id"], now + c.access_token_ttl_s * 1000
        await self.rec.put(K_ACCESS, sha(access), {
            "grant_id": gid, "user": grant["user"], "client_id": grant["client_id"],
            "client_name": grant["client_name"], "scopes": grant["scopes"], "aud": grant["resource"],
            "exp": exp, "created_at": now}, exp)  # fmt: skip
        await self.rec.put(
            K_REFRESH, sha(refresh), {"grant_id": gid, "used": False}, now + c.refresh_token_ttl_days * DAY_MS
        )
        return {"access_token": access, "token_type": "Bearer", "expires_in": c.access_token_ttl_s,
                "refresh_token": refresh, "scope": " ".join(grant["scopes"])}  # fmt: skip

    async def revoke_grant(self, gid: str) -> dict[str, Any] | None:
        g = await self.rec.take(K_GRANT, gid)
        await self.rec.del_where(K_ACCESS, "grant_id", gid)
        await self.rec.del_where(K_REFRESH, "grant_id", gid)
        return g  # type: ignore[no-any-return]

    async def token(self, request: Request, body: bytes) -> Response:
        app, now = request.app, now_ms()
        if not await self.hit(app, f"oauth-token:{client_ip(request)}", TOKEN_PER_IP):
            return self.oerr(429, "slow_down", "Pedidos demais; tente mais tarde.")
        f = form(body.decode("utf-8", "replace"))
        if any(k.endswith("\0dup") for k in f):
            return self.oerr(400, "invalid_request", "Parâmetro repetido.")
        client = await self.authenticate_client(request, f)
        if isinstance(client, Response):
            return client
        if not (ours := self.resource(request)):
            return self.no_issuer()
        if (r := f.get("resource")) is not None and canon(r) != canon(ours):
            return self.oerr(400, "invalid_target", f"resource deve ser {ours}.")

        def grant_err(d: str) -> Response:
            return self.oerr(400, "invalid_grant", d)

        gt = f.get("grant_type")
        if gt == "authorization_code":
            return await self.code_grant(f, client, ours, now, grant_err)
        if gt == "refresh_token":
            return await self.refresh_grant(f, client, now, grant_err)
        if gt is not None:
            return self.oerr(
                400, "unsupported_grant_type", "grant_type: authorization_code ou refresh_token."
            )
        return self.oerr(400, "invalid_request", "grant_type obrigatório.")

    async def code_grant(
        self,
        f: dict[str, str],
        client: dict[str, Any],
        ours: str,
        now: int,
        grant_err: Callable[[str], Response],
    ) -> Response:
        code = f.get("code", "")
        key, redirect_uri, verifier = sha(code), f.get("redirect_uri"), f.get("code_verifier", "")
        gid = f"g{secrets.token_hex(12)}"

        def consume(cur: Any) -> tuple[Change, tuple[str, Any]]:
            if cur is None:
                return Change.keep(), ("missing", None)
            if g := cur.get("used_grant"):
                return Change.set(cur | {"replayed": True}, now + 10 * 60_000), ("replay", g)
            bad = None
            if cur["client_id"] != client["id"]:
                bad = "Código emitido para outro cliente."
            elif redirect_uri != cur["redirect_uri"]:
                bad = "redirect_uri diferente da usada na autorização."
            elif not pkce_ok(verifier, cur["challenge"]):
                bad = "code_verifier inválido (PKCE)."
            elif canon(cur["resource"]) != canon(ours):
                bad = "resource diferente do autorizado."
            if bad:
                return Change.keep(), ("rejected", (bad, cur))
            return Change.set(cur | {"used_grant": gid}, now + 10 * 60_000), ("fresh", cur)

        kind, info = await self.rec.update(K_CODE, key, consume)
        if kind == "missing":
            return grant_err("Código inválido, vencido ou já usado.")
        if kind == "replay":
            if g := await self.revoke_grant(info):
                await self.audit("OAuth", "OAuth: código de autorização reutilizado — concessão revogada",
                                 f"{g.get('client_name', '')} · usuário {await self.user_label(g.get('user', ''))}")  # fmt: skip
            else:
                await self.audit(
                    "OAuth", "OAuth: código de autorização reutilizado", f"{client['name']} · {client['id']}"
                )
            return grant_err("Código inválido, vencido ou já usado.")
        if kind == "rejected":
            why, c = info
            await self.audit(await self.user_label(c.get("user", "")), "OAuth: troca de código recusada",
                             f"{client['name']} · {client['id']} · {why}")  # fmt: skip
            return grant_err(why)
        c, user = info, info["user"]
        if not await self.user_ok(user):
            return grant_err("Conta desativada.")
        grant = {"grant_id": gid, "client_id": client["id"], "client_name": c["client_name"], "client_kind": client["kind"],
                 "user": user, "scopes": c["scopes"], "resource": c["resource"], "redirect_uri": c["redirect_uri"],
                 "created_at": now, "last_used": now}  # fmt: skip
        await self.rec.put(K_GRANT, gid, grant, now + self.cfg.refresh_token_ttl_days * DAY_MS)
        cur = await self.rec.get(K_CODE, key)
        if cur and cur.get("replayed") is True:
            await self.revoke_grant(gid)
            return grant_err("Código reutilizado: a concessão foi revogada por segurança.")
        await self.audit(await self.user_label(user), "OAuth: token emitido (código de autorização)",
                         f"{client['name']} · {client['id']} · escopos: {' '.join(grant['scopes'])}")  # fmt: skip
        return self.json_res(200, await self.issue(grant))

    async def refresh_grant(
        self, f: dict[str, str], client: dict[str, Any], now: int, grant_err: Callable[[str], Response]
    ) -> Response:
        key = sha(f.get("refresh_token", ""))
        ttl = now + self.cfg.refresh_token_ttl_days * DAY_MS

        def rotate(cur: Any) -> tuple[Change, tuple[str, bool] | None]:
            if cur is None:
                return Change.keep(), None
            if cur.get("used") is True:
                return Change.keep(), (str(cur["grant_id"]), True)
            return Change.set(cur | {"used": True}, ttl), (str(cur["grant_id"]), False)

        r = await self.rec.update(K_REFRESH, key, rotate)
        if r is None:
            return grant_err("Refresh token inválido, vencido ou revogado.")
        gid, reused = r
        if reused:
            if g := await self.revoke_grant(gid):
                await self.audit("OAuth", "OAuth: refresh token reutilizado — concessão revogada",
                                 f"{g.get('client_name', '')} · usuário {await self.user_label(g.get('user', ''))} (possível roubo de token)")  # fmt: skip
                self.notify_reuse(g)
            return grant_err("Refresh token já usado: a concessão foi revogada por segurança.")

        def touch(cur: Any) -> tuple[Change, Any]:
            if cur is None:
                return Change.keep(), "Concessão revogada."
            if cur["client_id"] != client["id"]:
                return Change.keep(), "Refresh token de outro cliente."
            cur = cur | {"last_used": now}
            return Change.set(cur, ttl), cur

        grant = await self.rec.update(K_GRANT, gid, touch)
        if isinstance(grant, str):
            return grant_err(grant)
        if not await self.user_ok(grant["user"]):
            await self.revoke_grant(gid)
            return grant_err("Conta desativada.")
        if "scope" in f:
            want = self.scopes_of(f["scope"])
            if any(w not in grant["scopes"] for w in want):
                return self.oerr(400, "invalid_scope", "Escopo maior que o concedido.")
            grant = grant | {"scopes": want}
        return self.json_res(200, await self.issue(grant))

    def notify_reuse(self, g: dict[str, Any]) -> None:
        from hub.ux.inbox import item_of

        self.inbox.push(item_of(
            "notification", "platform", f'Acesso de "{g.get("client_name") or "aplicativo"}" revogado por segurança',
            body="Um refresh token deste aplicativo foi usado duas vezes (possível vazamento). O hub revogou o acesso; conecte o aplicativo de novo se foi você.",
            link="/account/access#apps", level="warning", at=now_ms(), source=f"oauth-reuse:{g.get('grant_id', '')}",
            to_users=[g["user"]]))  # fmt: skip

    async def revoke(self, request: Request, body: bytes) -> Response:
        if not await self.hit(request.app, f"oauth-revoke:{client_ip(request)}", REVOKE_PER_IP):
            return self.oerr(429, "slow_down", "Pedidos demais; tente mais tarde.")
        f = form(body.decode("utf-8", "replace"))
        client = await self.authenticate_client(request, f)
        if isinstance(client, Response):
            return client
        tok = f.get("token", "")
        key = sha(tok)
        if tok.startswith("hubr_"):
            r = await self.rec.get(K_REFRESH, key)
            owned = r and (g := await self.rec.get(K_GRANT, r["grant_id"])) and g["client_id"] == client["id"]
            if owned and r and (g := await self.revoke_grant(r["grant_id"])):
                await self.audit(
                    await self.user_label(g["user"]),
                    "OAuth: acesso revogado pelo aplicativo",
                    f"{client['name']} · {client['id']}",
                )
        elif (a := await self.rec.get(K_ACCESS, key)) and a["client_id"] == client["id"]:
            await self.rec.delete(K_ACCESS, key)
        return self.json_res(200, {})  # RFC 7009: 200 even for an unknown token

    # ---- resource server ---------------------------------------------------------------------------
    async def verify_access(self, tok: str, request: Request) -> dict[str, Any] | None:
        """OAuth token (`hubo_…`) valid for this `/mcp`: returns a synthetic token with an owner."""
        resource = self.resource(request)
        if not self.on or not tok.startswith("hubo_"):
            return None
        a = await self.rec.get(K_ACCESS, sha(tok))
        now = now_ms()
        if not a or not resource or canon(a["aud"]) != canon(resource) or a["exp"] <= now:
            return None
        gid = a["grant_id"]
        g = await self.rec.get(K_GRANT, gid)
        if not g:
            return None
        if now - g.get("last_used", 0) > 60_000:
            await self.rec.update(K_GRANT, gid, lambda cur: (
                Change.set(cur | {"last_used": now}, now + self.cfg.refresh_token_ttl_days * DAY_MS) if cur else Change.keep(), None))  # fmt: skip
        return {"id": f"oauth-{gid}", "name": f"OAuth: {a.get('client_name') or 'aplicativo'}", "hash": "",
                "prefix": "hubo_", "apps": [], "envs": [], "actions": list(a["scopes"]), "created_by": a["client_id"],
                "created_at": a.get("created_at", now), "expires_at": a["exp"], "revoked_at": None,
                "last_used": now, "owner": a["user"]}  # fmt: skip

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

    async def admin_action(self, path: str, body: bytes, actor: str) -> Response:
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

    templates: Jinja2Templates
    inbox: Any


def _unq(x: str) -> str:
    from urllib.parse import unquote_plus

    return unquote_plus(x)


def mount_oauth(app: FastAPI, templates: Jinja2Templates) -> None:
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
