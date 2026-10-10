"""Fábrica do app FastAPI: casca do hub (F0), assets do front-end atual e sondas."""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import re
import secrets
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from hub import __version__, devmode
from hub.admin.config import DEV_INFRA_PASSWORDS
from hub.admin.routes import mount_admin
from hub.admin.store import AdminStore
from hub.api.config import load_cfg
from hub.api.cors import Cors, machine_public
from hub.api.mcp import mount_mcp
from hub.api.oauth import OAuth, mount_oauth
from hub.api.records import Records
from hub.api.v1 import mount_v1
from hub.auth import (
    ABSOLUTE_MS,
    COOKIE,
    DUMMY_HASH,
    IDLE_MS,
    MAX_SESSIONS,
    SSO_COOKIE,
    AuthStore,
    admin_required,
    api_required,
    client_ip,
    control_allowed,
    entra,
    normalize_email,
    now_ms,
    owned_surface,
    permissions,
    positive_env,
    roles_for,
    token_action,
    token_covers,
    verify,
)
from hub.config import Settings, load_settings
from hub.connections import broker as broker_mod
from hub.connections.provision import ObjectStore
from hub.connections.routes import broker_url, mount_connections
from hub.connections.store import ConnStore
from hub.gateway.identity import user_sig_key
from hub.gateway.proxy import Gateway, GwOptions
from hub.gateway.registry import load_registry
from hub.gateway.routes import mount_gateway
from hub.insight.routes import mount_insight
from hub.insight.runtime import Insight
from hub.models import LoginInput, TokenCreateInput
from hub.ops.background import Background
from hub.ops.newapp import load_dynamic
from hub.ops.routes import mount_ops
from hub.state import Config as StateConfig
from hub.state import SessionLimits, SessionRow, StateDb
from hub.ux import search as srch
from hub.ux.account_routes import mount_account, safe_next
from hub.ux.inbox import InboxStore
from hub.ux.inbox_routes import bell_of, mount_inbox
from hub.ux.routes import icon, mount_ux
from hub.ux.shell import eyebrow_for, i18n_for, nav_for, platform_palette, tabs_for, theme_of, visible_nav
from hub.ux.store import UxStore
from hub.ux.texts import js_catalog, resolve, t, tn, tr, tr_json
from hub.vault import Vault

_WEB = Path(__file__).parent / "web"
_THEME = "#0a2848"
# Faixa discreta do modo staging (cookie `hub_env`), servida junto com o tema.
STAGING_BAR_CSS = (
    ".hub-staging-bar{display:flex;gap:8px;justify-content:center;align-items:center;padding:4px 16px;"
    "font-size:12px;background:var(--color-warning-soft);color:var(--color-text);"
    "border-bottom:1px solid var(--color-warning-shape)}.hub-staging-bar a{color:var(--color-text);font-weight:600}"
    # Nas telas de app o workspace cobre o #app-root: a faixa vira um selo flutuante (F17).
    "html[data-hub-app] .hub-staging-bar{position:fixed;left:50%;bottom:12px;transform:translateX(-50%);"
    "z-index:var(--z-header);border:1px solid var(--color-warning-shape);border-radius:999px;"
    "box-shadow:0 4px 14px rgb(0 0 0/.15)}\n"
)

# Mesma CSP do hub Rust (apps/hub-web/src/auth.rs); 'self' cobre /assets/ e /hub-sw.js.
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "img-src 'self' data: blob:; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
_HEADERS = {
    "content-security-policy": _CSP,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "same-origin",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": "camera=(self), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
}
_SCRIPTS = (
    "hub-ux.js",
    "hub-i18n.js",
    "logshare.js",
    "admin.js",
    "ops.js",
    "insights.js",
    "ux.js",
    "devdocs.js",
)
_ICONS = [
    {"purpose": "any", "sizes": "192x192", "src": "/assets/icon-192.png", "type": "image/png"},
    {"purpose": "any", "sizes": "512x512", "src": "/assets/icon-512.png", "type": "image/png"},
    {"purpose": "maskable", "sizes": "512x512", "src": "/assets/icon-maskable-512.png", "type": "image/png"},
]


def initials(name: str) -> str:
    words = name.split()
    if not words:
        return "?"
    return words[0][:2].upper() if len(words) == 1 else (words[0][0] + words[-1][0]).upper()


def _find(settings: Settings, rel: str) -> Path | None:
    for base in settings.asset_dirs:
        p = (base / rel).resolve()
        if p.is_file() and p.is_relative_to(base.resolve()):
            return p
    return None


def create_app(settings: Settings | None = None, gateway: Gateway | None = None) -> FastAPI:
    devmode.startup_check()
    settings = settings or load_settings()
    apicfg = load_cfg(settings.config)
    gateway = gateway or Gateway(load_registry(), GwOptions(user_sig_key=user_sig_key()))
    data_dir = Path(os.getenv("HUB_DATA_DIR", Path(__file__).resolve().parents[2] / "platform/data"))
    load_dynamic(data_dir, gateway.registry)  # F22: apps novos implantados pelo hub
    auth_store = AuthStore(data_dir / "auth.json")
    vault = Vault.open(data_dir)
    admin = AdminStore(data_dir, vault, apps=gateway.registry.ids())
    conns = ConnStore(data_dir, vault, admin)
    broker_addr = os.getenv("HUB_BROKER_ADDR", "").strip()
    broker_on = broker_addr not in ("", "0", "off")
    srch.token_source = lambda app_id: admin.tokens.get(app_id)

    async def startup_admin() -> None:
        """Variáveis iniciais, token interno de cada app (cofre) e variáveis geridas das conexões."""
        actor = "plataforma"
        if path := os.getenv("HUB_APP_ENV_FILE"):
            with suppress(OSError, ValueError):
                n = await admin.cfg.import_bootstrap(
                    actor, json.loads(await asyncio.to_thread(Path(path).read_text, encoding="utf-8"))
                )
                if n:
                    print(f"hub: {n} variáveis iniciais importadas de {path}")

        def seed_of(app_id: str) -> str | None:
            tok = os.getenv("HUB_INTERNAL_TOKEN_" + app_id.upper().replace("-", "_"), "").strip()
            return tok if len(tok) >= 16 else None

        # Rotação das senhas de infraestrutura: valores de dev nas variáveis viram as senhas configuradas.
        rotated = await admin.cfg.replace_secret_values(
            actor,
            {
                d: os.getenv(k, "").strip()
                for k, d in DEV_INFRA_PASSWORDS.items()
                if len(os.getenv(k, "").strip()) >= 16
            },
        )
        if rotated:
            print(f"hub: senhas de infraestrutura atualizadas em {len(rotated)} variável(is)")
        await admin.cfg.ensure_internal_tokens(actor, seed_of)
        await conns.sync_env(actor, broker_url())
        app.state.roles = roles_for(data_dir)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = StateConfig.from_env()
        db = await StateDb.connect(cfg) if cfg else None
        auth_store.db = db
        admin.db, admin.chain.db, conns.db = db, db, db
        if (ops_store := getattr(app.state, "ops", None)) is not None:
            ops_store.db = db  # F19: `ops.json` sob trava entre réplicas
        await admin.reconcile()
        if db:
            async with auth_store.locked(True) as data:
                old_sessions = list(data["sessions"])
                for session in old_sessions:
                    await db.session_create(
                        SessionRow(**session), SessionLimits(IDLE_MS, ABSOLUTE_MS, MAX_SESSIONS)
                    )
                data["sessions"].clear()
        await auth_store.bootstrap()
        await startup_admin()
        insight.start(gateway.client)
        start_dev_watch()
        bg = Background(app, gateway.client, data_dir)
        await bg.start(db, float(os.getenv("HUB_OUTBOX_POLL_S", "5") or 5))
        broker = None
        if broker_on:
            broker = await broker_mod.serve(
                broker_mod.build_broker(conns, admin, gateway.opts.user_sig_key or user_sig_key()),
                broker_addr,
            )
        yield
        if (w := getattr(app.state, "dev_watch", None)) is not None:
            w.cancel()
        await bg.stop()
        await insight.stop()
        if broker:
            await broker_mod.stop(*broker)
        await gateway.aclose()
        if db:
            await db.close()

    app = FastAPI(title=settings.brand, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.admin, app.state.conns, app.state.broker_enabled = admin, conns, broker_on
    app.state.apicfg = apicfg
    start_dev_watch = devmode.mount(app)
    app.state.records = Records(lambda: auth_store.db)
    app.state.oauth = OAuth(apicfg, auth_store, admin, app.state.records)
    app.state.auth = auth_store
    app.state.roles = roles_for(data_dir)
    app.state.login_failures = {}
    app.state.gateway, app.state.data_dir = gateway, data_dir
    app.state.ux = UxStore(data_dir / "ux.json")
    app.state.inbox = InboxStore(data_dir / "inbox.json")
    templates = Jinja2Templates(directory=_WEB / "templates")
    cast(dict[str, Any], templates.env.globals)["dev_reload"] = devmode.reload_enabled
    insight = Insight(data_dir, vault, admin, gateway.registry, app.state.ux)
    app.state.insight = insight
    gateway.extra_headers = lambda request, app_id, ident: insight.upstream_headers(
        request, app_id, ident.user, list(ident.roles)
    )

    def public_status() -> set[str] | None:
        """Apps da status page pública (`None` = página pública desligada em Configurações)."""
        try:
            cfg = json.loads((data_dir / "admin.json").read_text(encoding="utf-8")).get("settings", {})
        except (OSError, ValueError):
            return None
        return set(cfg.get("status_public_apps", [])) if cfg.get("status_public") else None

    @app.middleware("http")
    async def translate_errors(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Mensagens de erro JSON do servidor no idioma do usuário (como a camada i18n do hub Rust)."""
        resp = await call_next(request)
        path = request.url.path
        ct = resp.headers.get("content-type", "").split(";")[0]
        if (
            resp.status_code < 400
            or ct not in ("application/json", "application/problem+json")
            or path.startswith(("/_frag/", "/assets/", "/oauth/", "/.well-known/", "/apps/"))
            or "content-disposition" in resp.headers
        ):
            return resp
        lang = getattr(request.state, "lang", None) or resolve("", request.headers.get("accept-language"))
        if lang == "pt-BR":
            return resp
        raw = b"".join([c async for c in cast(Any, resp).body_iterator])
        headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
        try:
            data = json.loads(raw)
            if tr_json(data, lang):
                raw = json.dumps(data, ensure_ascii=False).encode()
        except ValueError:
            pass
        headers["content-language"] = lang
        return Response(raw, resp.status_code, headers)

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        resp = await call_next(request)
        path = request.url.path
        if (
            path in ("/login", "/account/password", "/oauth/authorize")
            or path.startswith("/account/agent-approval/")
        ) and resp.headers.get("content-type", "").startswith("text/html"):
            # Telas de credencial abrem em grupo de navegação próprio: `window.open` de um script de app
            # recebe uma janela sem acesso ao DOM (não lê os campos).
            resp.headers["cross-origin-opener-policy"] = "noopener-allow-popups"
        for k, v in _HEADERS.items():
            resp.headers.setdefault(k, v)
        return resp

    @app.middleware("http")
    async def authentication(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path, method = request.url.path, request.method
        request.state.lang = resolve("", request.headers.get("accept-language"))
        request.state.theme = "light"
        public = (
            path == "/login"
            or path.startswith(("/auth/entra/",))
            or path.startswith(
                (
                    "/assets/",
                    "/healthz",
                    "/readyz",
                    "/hub-sw.js",
                    "/manifest.webmanifest",
                    "/offline",
                    "/dev/cli/",
                )
            )
        )
        if machine_public(path, request.app.state.apicfg.mcp.path):  # descoberta/token/registro: sem sessão
            c = request.app.state.apicfg.mcp
            if not control_allowed(request, c.public_network, c.path):
                return Response("Acesso negado: origem não permitida.", status_code=403)
            return await call_next(request)
        if public:
            return await call_next(request)
        facade = request.scope.get("hub_facade")  # chamada em processo da API v1/MCP (já autorizada)
        store = cast(AuthStore, request.app.state.auth)
        user: dict[str, Any] | None = None
        token: dict[str, Any] | None = None
        authz = request.headers.get("authorization", "")
        cfg = request.app.state.apicfg
        if facade:
            user, token = facade
        elif not control_allowed(request, cfg.mcp.public_network, cfg.mcp.path):
            return Response("Acesso negado: origem não permitida.", status_code=403)
        elif authz.lower().startswith("bearer "):
            value = authz[7:].strip()
            if value.startswith("hubo_"):
                token = (
                    await request.app.state.oauth.verify_access(value, request)
                    if owned_surface(method, path, cfg.mcp.path) == "mcp"
                    else None
                )
            else:
                token = await store.bearer(value)
            if token:
                surface = owned_surface(method, path, cfg.mcp.path)
                action = token_action(method, path)
                if not (
                    path.startswith("/api/me/")
                    or (surface is not None and token_covers(token, surface))
                    or (action is not None and action in token["actions"])
                ):
                    token = None
                elif token.get("owner"):
                    async with store.locked() as d:
                        user = next(
                            (
                                u.copy()
                                for u in d["users"]
                                if u["id"] == token["owner"] and u["active"] and not u["must_change_password"]
                            ),
                            None,
                        )
                else:
                    user = {
                        "id": token["id"],
                        "email": "",
                        "name": f"token:{token['name']}",
                        "roles": [],
                        "active": True,
                    }
        else:
            cookie = request.cookies.get(COOKIE)
            if cookie:
                try:
                    user = await store.session(cookie)
                except Exception:
                    return Response("State database unavailable", status_code=503)
        if not user and path == "/status" and method == "GET" and public_status() is not None:
            return await call_next(request)
        if (
            not user
            and not authz
            and method in ("GET", "HEAD")
            and (
                path in ("/developers", "/llms.txt", "/dev/llms.txt")
                or path.startswith(("/developers/", "/dev/docs/"))
            )
            and os.getenv("HUB_PUBLIC_DOCS", "1") != "0"
        ):
            return await call_next(
                request
            )  # documentação pública (só leitura; HUB_PUBLIC_DOCS=0 exige login)  # status page pública (Configurações), só leitura
        if not user or (token is None and authz):
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
        if method not in ("GET", "HEAD", "OPTIONS") and not facade:
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            fetch_site = request.headers.get("sec-fetch-site")
            bearer_only = token is not None  # a identidade vem do token (o cookie, se vier junto, é ignorado)
            # `Sec-Fetch-Site: same-origin` basta (como no Rust): com `Referrer-Policy: no-referrer` (consentimento
            # OAuth) o navegador manda `Origin: null` num POST do próprio hub.
            if (
                not bearer_only
                and fetch_site != "same-origin"
                and (
                    fetch_site == "cross-site"
                    or (not origin and fetch_site not in ("same-site", "none"))
                    or (origin and origin.split("://", 1)[-1] != host)
                )
            ):
                return Response("Requisição bloqueada.", status_code=403)
        request.state.user, request.state.token = user, token
        prefs = request.app.state.ux.prefs(user["id"])
        request.state.lang = resolve(prefs.lang, request.headers.get("accept-language"))
        request.state.theme = prefs.theme
        perms, apps = permissions(user, request.app.state.roles)
        if apps is not None:  # acessos concedidos por pedido aprovado na Inbox
            apps = [*apps, *request.app.state.inbox.grants_for(user["id"])]
        request.state.permissions, request.state.apps = perms, apps
        required: tuple[str, ...] | None = None
        segs = path.strip("/").split("/")
        if path == "/" or segs[0] in ("applications", "apps", "_frag"):
            required = ("acessar_aplicacoes",)
        elif segs[0] == "admin":
            required = admin_required(segs[1] if len(segs) > 1 else "")
        elif segs[0] == "api":
            required = (
                () if len(segs) > 1 and segs[1] in ("me", "v1", "docs") else api_required(method, segs[1:])
            )
        elif segs[0] in ("logout", "account", "status", "inbox", "search", "oauth", "mcp"):
            required = ()
        else:
            required = ("acessar_aplicacoes",)
        if token and token_action(method, path) in token.get("actions", []):
            required = ()  # token de CI/`hub deploy` (upload/status): a ação do token é a autorização
        app_id = segs[1] if segs[0] in ("apps", "_frag") and len(segs) > 1 else segs[0]
        denied = required is not None and required and not any(p in perms for p in required)
        denied = denied or (
            required == ("acessar_aplicacoes",)
            and segs[0] in ("apps", "_frag")
            and apps is not None
            and app_id not in apps
        )
        denied = denied or bool(
            token and token.get("apps") and segs[0] in ("apps", "_frag") and app_id not in token["apps"]
        )  # token restrito a apps
        if denied:
            if path.startswith("/api/"):
                return JSONResponse({"error": "Acesso negado."}, status_code=403)
            return templates.TemplateResponse(
                request,
                "forbidden.html",
                page_ctx(request)
                | {
                    "message": "Você não tem permissão para acessar esta página.",
                    "eyebrow": eyebrow_for(path, getattr(request.state, "lang", "pt-BR")),
                },
                status_code=403,
            )
        if user.get("must_change_password") and path != "/account/password" and path != "/logout":
            return RedirectResponse("/account/password", status_code=303)
        response = await call_next(request)
        if not facade:
            request.app.state.ux_track(request, response.status_code)
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        # F1: checar o Postgres; na F0 o processo de pé é a prontidão.
        return {"status": "ready"}

    def brand_color() -> str:
        try:
            cfg = json.loads((data_dir / "admin.json").read_text(encoding="utf-8")).get("settings", {})
        except (OSError, ValueError):
            return ""
        c = str(cfg.get("brand_color", "")).strip().lower()
        return c if re.fullmatch(r"#[0-9a-f]{6}", c) else ""

    @app.get("/assets/theme.css")
    async def theme_css() -> Response:
        """Camada de tema da instalação (white label): só tokens semânticos, a cor vem validada como #rrggbb."""
        out = "/* Tema da instalação (Plataforma › Configurações). */\n" + STAGING_BAR_CSS
        if c := brand_color():
            dark = f"--color-accent: color-mix(in srgb, {c} 45%, #ffffff);"
            out += f":root {{ --color-accent: {c}; --c-blue-31: {c}; }}\n"
            out += f':root[data-theme="dark"] {{ {dark} }}\n'
            out += f'@media (prefers-color-scheme: dark) {{ :root[data-theme="auto"] {{ {dark} }} }}\n'
        return Response(out, media_type="text/css", headers={"cache-control": "no-cache"})

    @app.get("/assets/i18n/{file}")
    async def i18n_catalog(file: str) -> Response:
        if file not in ("en.json", "es.json"):
            raise HTTPException(404)
        return JSONResponse(js_catalog(file[:-5]), headers={"cache-control": "no-cache"})

    asset_cache: dict[
        str, tuple[float, int, int, bytes, str, str]
    ] = {}  # path → (conferido, mtime_ns, size, corpo, tipo, etag)

    def asset(path: str) -> tuple[bytes, str, str] | None:
        """Corpo, tipo e ETag; a pasta dos assets costuma estar num bind mount lento (Windows/Docker) —
        guarda em memória e só reconfere o arquivo a cada 2 s (modo dev: sempre)."""
        now = time.monotonic()
        hit = asset_cache.get(path)
        if hit and not devmode.enabled() and now - hit[0] < 2:
            return hit[3], hit[4], hit[5]
        f = _find(settings, path)
        if f is None:
            return None
        st = f.stat()
        if hit and hit[1] == st.st_mtime_ns and hit[2] == st.st_size:
            asset_cache[path] = (now, *hit[1:])
            return hit[3], hit[4], hit[5]
        body = f.read_bytes()
        mt = (
            "font/woff2"
            if f.suffix == ".woff2"
            else (mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        )
        etag = f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}"'
        asset_cache[path] = (now, st.st_mtime_ns, st.st_size, body, mt, etag)
        return body, mt, etag

    @app.get("/assets/{path:path}")
    async def assets(request: Request, path: str) -> Response:
        got = await asyncio.to_thread(asset, path)
        if got is None:
            raise HTTPException(404)
        body, mt, etag = got
        # Como o hub Rust: fontes 7 dias, vendor 1 dia, o resto revalida (ETag → 304); modo dev: sem cache.
        cache = (
            "no-store"
            if devmode.enabled()
            else "public, max-age=604800"
            if path.startswith("fonts/")
            else "public, max-age=86400"
            if path.startswith("vendor/")
            else "no-cache"
        )
        headers = {"cache-control": cache, "etag": etag}
        if cache == "no-cache":  # CDN (ex.: Cloudflare) não guarda: senão serve CSS/JS antigo após o deploy
            headers["cdn-cache-control"] = "no-store"
        if cache == "no-cache" and etag in request.headers.get("if-none-match", ""):
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=mt, headers=headers)

    @app.get("/hub-sw.js")
    async def service_worker() -> Response:
        f = _find(settings, "hub-sw.js")
        if f is None:
            raise HTTPException(404)
        headers = {"cache-control": "no-cache", "service-worker-allowed": "/"}
        return FileResponse(f, media_type="text/javascript", headers=headers)

    @app.get("/manifest.webmanifest")
    async def manifest(request: Request, start: str = "") -> JSONResponse:
        lang = resolve("", request.headers.get("accept-language"))
        ents = sorted(gateway.registry.entries(), key=lambda e: (e.id != "coletor", e.id))
        s = start.strip()
        sid = s.removeprefix("/apps/") if s.startswith("/apps/") else s.removeprefix("/")
        data = {
            "id": "/",
            "name": settings.brand,
            "short_name": settings.brand.split()[-1] if settings.brand.split() else "Hub",
            "lang": lang,
            "description": t("pwa.description", lang),
            "start_url": f"/apps/{sid}" if any(e.id == sid for e in ents) else "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "any",
            "background_color": _THEME,
            "theme_color": _THEME,
            "icons": _ICONS,
            "shortcuts": [
                {
                    "name": e.name,
                    "short_name": e.display.get("short", e.name),
                    "url": f"/apps/{e.id}",
                    "icons": [{"src": "/assets/icon-192.png", "sizes": "192x192", "type": "image/png"}],
                }
                for e in ents[:4]
            ],
        }
        return JSONResponse(
            data, media_type="application/manifest+json", headers={"cache-control": "no-cache"}
        )

    @app.get("/offline", response_class=HTMLResponse)
    async def offline(request: Request) -> Response:
        lang = resolve("", request.headers.get("accept-language"))
        return templates.TemplateResponse(
            request,
            "offline.html",
            {
                "brand": settings.brand,
                "lang": lang,
                "title": t("pwa.offline_title", lang),
                "copy": t("pwa.offline_copy", lang),
                "retry": t("pwa.offline_retry", lang),
            },
            headers={"cache-control": "no-cache"},
        )

    def app_palette(request: Request, lang: str) -> str:
        """Apps do usuário primeiro, depois as páginas da plataforma (atalho Ctrl+K)."""
        allowed = getattr(request.state, "apps", None)
        apps = [
            {
                "title": e.name,
                "subtitle": e.display.get("description", ""),
                "url": f"/apps/{e.id}",
                "kind": t("shell.apps", lang),
                "score": 0.0,
            }
            for e in gateway.registry.entries()
            if allowed is None or e.id in allowed
        ]
        return json.dumps([*apps, *platform_palette(lang)], ensure_ascii=False, separators=(",", ":"))

    def page_ctx(request: Request, body_class: str = "rail-expanded") -> dict[str, Any]:
        principal = getattr(request.state, "user", {"name": "Visitante"})
        lang = getattr(request.state, "lang", "pt-BR")
        theme, color_scheme = theme_of(getattr(request.state, "theme", ""))
        user = {"name": principal["name"], "initials": initials(principal["name"])}
        unread, bell = bell_of(request) if hasattr(request.state, "user") else (0, [])
        # Modo do rail guardado à parte dentro dos apps e fora (cookie `hub_rail_<ctx>`, gravado pelo hub-ux.js):
        # o servidor já desenha a página no modo certo.
        in_app = "in-app" in body_class
        in_docs = request.url.path.startswith("/developers")  # docs: 3 colunas, rail recolhido por padrão
        rail_ctx = "app" if in_app else "docs" if in_docs else "hub"
        chosen = request.cookies.get(f"hub_rail_{rail_ctx}", "")
        default = "hidden" if in_app else "collapsed" if in_docs else "expanded"
        mode = chosen if chosen in ("expanded", "collapsed", "hidden") else default
        body_class = " ".join([f"rail-{mode}", *[c for c in body_class.split() if not c.startswith("rail-")]])
        rail = f"js.rail_{mode}"
        return {
            "body_class": body_class,
            "rail_ctx": rail_ctx,
            "staging": request.cookies.get("hub_env") == "staging",
            "brand": settings.brand,
            "user": user,
            "perms": " ".join(getattr(request.state, "permissions", [])),
            "nav": visible_nav(nav_for(lang), getattr(request.state, "permissions", [])),
            "path": request.url.path,
            "tabs": tabs_for(
                request.url.path,
                request.query_params.get("tab", ""),
                getattr(request.state, "permissions", []),
            ),
            "manifest_href": "/manifest.webmanifest"
            + (
                f"?start=/apps/{m[1]}"
                if (m := re.match(r"^/apps/([a-z0-9-]+)(?:/|$)", request.url.path))
                else ""
            ),
            "i18n": i18n_for(lang),
            "palette": app_palette(request, lang),
            "scripts": _SCRIPTS,
            "theme_color": _THEME,
            "lang": lang,
            "theme": theme,
            "color_scheme": color_scheme,
            "t": lambda k: t(k, lang),
            "tn": lambda k, n: tn(k, n, lang),
            "tr": lambda s: tr(s, lang),
            "tour": request.app.state.ux.prefs(request.state.user["id"]).tour
            if hasattr(request.state, "user")
            else "done",
            "unread": unread,
            "bell": bell,
            "rail_label": f"{t('js.rail_menu', lang)}: {t(rail, lang)}",
        }

    try:
        sso, app.state.sso_reason = entra.load(settings.config), ""
    except entra.SsoError as e:
        sso, app.state.sso_reason = None, str(e)
        print(f"[hub] {e}", flush=True)
    local_mode = entra.local_login_mode(settings.config) if sso else "on"
    app.state.sso, app.state.sso_local = sso, local_mode

    def login_page(
        request: Request, next_url: str, error: str = "", email: str = "", status: int = 200
    ) -> Response:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "brand": settings.brand,
                "next": next_url,
                "error": error,
                "email": email,
                "sso": sso is not None,
                "local": local_mode != "off",
                "lang": request.state.lang,
                "tr": lambda s: tr(s, request.state.lang),
            },
            status_code=status,
        )

    def set_session_cookie(request: Request, resp: Response, cookie: str) -> None:
        setting = os.getenv("HUB_COOKIE_SECURE", "").strip().lower()
        secure = (
            setting in ("1", "true", "yes", "sim", "on")
            if setting and setting != "auto"
            else request.url.scheme == "https"
            or (
                os.getenv("HUB_TRUST_PROXY_HEADERS") == "1"
                and request.headers.get("x-forwarded-proto") == "https"
            )
            or (not os.getenv("HUB_DEMO_USERS") and os.getenv("HUB_PUBLIC_URL", "").startswith("https://"))
        )
        resp.set_cookie(
            COOKIE,
            cookie,
            max_age=ABSOLUTE_MS // 1000,
            path="/",
            httponly=True,
            samesite="lax",
            secure=secure,
        )

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request, next: str = "/") -> Response:
        if request.cookies.get(COOKIE) and await auth_store.session(request.cookies[COOKIE]):
            return RedirectResponse(safe_next(next), status_code=303)
        return login_page(request, next)

    def sso_redirect_uri(request: Request) -> str:
        base = os.getenv("HUB_PUBLIC_URL", "").strip().rstrip("/") or str(request.base_url).rstrip("/")
        return f"{base}/auth/entra/callback"

    @app.get("/auth/entra/login")
    async def entra_login(request: Request, next: str = "/") -> Response:
        if not sso:
            return RedirectResponse("/login", status_code=303)
        url, pending = sso.authorize_url(sso_redirect_uri(request))
        resp = RedirectResponse(url, status_code=303)
        resp.set_cookie(
            SSO_COOKIE,
            json.dumps({**pending, "next": safe_next(next)}, separators=(",", ":")),
            max_age=600,
            path="/auth/entra/",
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https" or os.getenv("HUB_PUBLIC_URL", "").startswith("https://"),
        )
        return resp

    @app.get("/auth/entra/callback")
    async def entra_callback(request: Request) -> Response:
        if not sso:
            return RedirectResponse("/login", status_code=303)
        q = request.query_params
        try:
            pending = json.loads(request.cookies.get(SSO_COOKIE, "{}"))
        except ValueError:
            pending = {}
        next_url = safe_next(str(pending.get("next", "/")))
        admin_store = getattr(app.state, "admin", None)
        ip = client_ip(request)
        error = ""
        user: dict[str, Any] | None = None
        try:
            if q.get("error"):
                raise entra.SsoError("O Microsoft Entra ID recusou o login.")
            state = q.get("state", "")
            if not state or not secrets.compare_digest(state, str(pending.get("state", ""))):
                raise entra.SsoError("Sessão de login expirada; tente de novo.")
            id_token = await sso.exchange(
                q.get("code", ""), str(pending["verifier"]), sso_redirect_uri(request)
            )
            claims = await sso.validate(id_token, str(pending.get("nonce", "")))
            raw_email = str(claims.get("email") or claims.get("preferred_username") or "")
            try:
                email = normalize_email(raw_email)
            except ValueError as e:
                raise entra.SsoError("A conta Microsoft não informou um e-mail válido.") from e
            if not sso.domain_ok(email):
                raise entra.SsoError("Este domínio de e-mail não tem acesso à plataforma.")
            user, created = await auth_store.sso_login(
                str(claims["tid"]),
                str(claims.get("oid") or claims["sub"]),
                email,
                str(claims.get("name") or ""),
                sso.roles_for(claims),
                sso.default_role,
            )
            if user is None:
                raise entra.SsoError("Usuário desativado. Fale com o administrador.")
            if admin_store and created:
                await admin_store.audit(
                    user["name"], "Usuário criado pelo SSO", f"{email} (Entra ID)", icon="users"
                )
        except (entra.SsoError, httpx.HTTPError, KeyError, ValueError) as e:
            error = (
                str(e)
                if isinstance(e, entra.SsoError)
                else "Não foi possível concluir o login com a Microsoft."
            )
        if user is None:
            if admin_store:
                await admin_store.audit("SSO", "Login SSO recusado", f"{error} · via {ip}", icon="users")
            resp = login_page(request, next_url, error, status=401)
            resp.delete_cookie(SSO_COOKIE, path="/auth/entra/")
            return resp
        if admin_store:
            await admin_store.audit(
                user["name"], "Acesso à plataforma", f"Login SSO (Entra ID) via {ip}", icon="users"
            )
        insight.analytics.record(user["id"], "login")
        cookie = await auth_store.session_new(user, request.cookies.get(COOKIE), method="entra")
        resp = RedirectResponse(next_url, status_code=303)
        set_session_cookie(request, resp, cookie)
        resp.delete_cookie(SSO_COOKIE, path="/auth/entra/")
        return resp

    @app.post("/login")
    async def login_post(request: Request) -> Response:
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        try:
            login = LoginInput.model_validate(
                {"email": form.get("email", [""])[0], "password": form.get("password", [""])[0]}
            )
        except ValidationError:
            return Response("Dados de acesso inválidos.", status_code=400)
        email, password = login.email.strip().lower(), login.password
        next_url = form.get("next", ["/"])[0]
        fetch_site = request.headers.get("sec-fetch-site")
        if (
            fetch_site == "cross-site"
            or (not request.headers.get("origin") and fetch_site not in ("same-origin", "same-site", "none"))
            or (
                request.headers.get("origin")
                and request.headers["origin"].split("://", 1)[-1] != request.headers.get("host")
            )
        ):
            return Response("Requisição bloqueada.", status_code=403)
        db = auth_store.db
        ip = client_ip(request)
        window = 15 * 60_000
        limits = (
            positive_env("HUB_LOGIN_RATE_ACCOUNT", 5, 1000),
            positive_env("HUB_LOGIN_RATE_IP", 30, 10_000),
        )
        buckets = (f"acct:{ip}|{email}", f"ip:{ip}")
        try:
            counts = (
                [await db.rate_peek("login", key, window, now_ms()) for key in buckets]
                if db
                else [
                    len([at for at in app.state.login_failures.get(key, []) if at > now_ms() - window])
                    for key in buckets
                ]
            )
        except Exception:
            return Response("State database unavailable", status_code=503)
        if any(count >= limit for count, limit in zip(counts, limits, strict=True)):
            verify(password, DUMMY_HASH)
            error, status = "Muitas tentativas de acesso. Aguarde alguns minutos e tente novamente.", 429
        else:
            user = await auth_store.authenticate(email, password)
            if user and (
                local_mode == "off" or (local_mode == "admins" and "Administrador" not in user["roles"])
            ):
                user = None
            if user:
                if admin_store := getattr(app.state, "admin", None):
                    await admin_store.audit(
                        user["name"], "Acesso à plataforma", f"Login via {ip}", icon="users"
                    )
                insight.analytics.record(user["id"], "login")
                cookie = await auth_store.session_new(user, request.cookies.get(COOKIE))
                target = (
                    next_url if next_url.startswith("/") and not next_url.startswith(("//", "/\\")) else "/"
                )
                if user.get("must_change_password"):
                    target = "/account/password?next=" + quote(target, safe="/")
                resp = RedirectResponse(target, status_code=303)
                set_session_cookie(request, resp, cookie)
                with suppress(OSError):
                    (data_dir / "bootstrap-admin.txt").unlink()
                return resp
            if db:
                for key, limit in zip(buckets, limits, strict=True):
                    await db.rate_hit("login", key, window, limit, now_ms())
            else:
                for key in buckets:
                    app.state.login_failures.setdefault(key, []).append(now_ms())
            error, status = "E-mail ou senha incorretos.", 401
        return login_page(request, next_url, error, email, status)

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        if value := request.cookies.get(COOKIE):
            if admin_store := getattr(app.state, "admin", None):
                await admin_store.audit(
                    request.state.user["name"],
                    "Sessão encerrada",
                    f"Logout via {client_ip(request)}",
                    icon="users",
                )
            await auth_store.logout(value)
        resp = RedirectResponse("/login?m=saiu", status_code=303)
        resp.delete_cookie(COOKIE, path="/")
        return resp

    def password_page(request: Request, error: str = "", next_url: str = "/", status: int = 200) -> Response:
        lang = request.state.lang
        ctx = {
            "brand": settings.brand,
            "error": error,
            "next": next_url,
            "lang": lang,
            "tr": lambda s: tr(s, lang),
        }
        return templates.TemplateResponse(
            request,
            "password.html",
            ctx | {"cancel": not request.state.user["must_change_password"]},
            status_code=status,
        )

    @app.get("/account/password", response_class=HTMLResponse)
    async def password_get(request: Request, next: str = "/") -> Response:
        return password_page(request, next_url=safe_next(next))

    @app.post("/account/password")
    async def password_post(request: Request) -> Response:
        user = request.state.user
        f = parse_qs((await request.body()).decode("utf-8", "replace"))
        current, new = f.get("current", [""])[0], f.get("new", [""])[0]
        target = safe_next(f.get("next", ["/"])[0])
        if new != f.get("confirm", [""])[0]:
            return password_page(request, "As senhas não coincidem.", target, 400)
        try:
            if not await auth_store.change_password(user, current, new):
                return password_page(request, "Senha atual incorreta.", target, 400)
        except ValueError as e:
            return password_page(request, str(e), target, 400)
        # As sessões anteriores caíram; esta continua com um id novo e volta para onde a pessoa estava.
        resp = RedirectResponse(target, status_code=303)
        resp.set_cookie(
            COOKIE,
            await auth_store.session_new(user),
            max_age=ABSOLUTE_MS // 1000,
            path="/",
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return resp

    @app.get("/api/me/tokens")
    async def my_tokens(request: Request) -> dict[str, Any]:
        if request.state.token:
            raise HTTPException(403, "Tokens pessoais só podem ser geridos com a sessão.")
        async with auth_store.locked() as d:
            return {
                "items": [
                    {k: v for k, v in t.items() if k != "hash"}
                    | {
                        "active": t.get("revoked_at") is None
                        and t.get("expires_at", 0) > time.time_ns() // 1_000_000
                    }
                    for t in d["tokens"]
                    if t.get("owner") == request.state.user["id"]
                ]
            }

    @app.post("/api/me/tokens")
    async def new_token(request: Request) -> JSONResponse:
        if request.state.token:
            return JSONResponse(
                {"error": "Tokens pessoais só podem ser geridos com a sessão."}, status_code=403
            )
        try:
            data = TokenCreateInput.model_validate(await request.json())
        except (ValidationError, ValueError):
            return JSONResponse({"error": "Dados de token inválidos."}, status_code=400)
        actions, apps = data.actions, data.apps
        if request.state.apps is not None and any(app_id not in request.state.apps for app_id in apps):
            return JSONResponse({"error": "Você não tem acesso a um dos apps informados."}, status_code=403)
        secret, item = await auth_store.create_token(
            request.state.user,
            data.name,
            apps,
            actions,
            data.expires_days,
        )
        if admin_store := getattr(app.state, "admin", None):
            await admin_store.audit(
                request.state.user["name"], "Token pessoal criado", item["name"], icon="users"
            )
        return JSONResponse(
            {"token": secret, "item": {k: v for k, v in item.items() if k != "hash"}}, status_code=201
        )

    @app.post("/api/me/tokens/{token_id}/revoke")
    async def revoke_token(request: Request, token_id: str) -> JSONResponse:
        if request.state.token:
            return JSONResponse(
                {"error": "Tokens pessoais só podem ser geridos com a sessão."}, status_code=403
            )
        async with auth_store.locked(True) as d:
            item = next(
                (
                    t
                    for t in d["tokens"]
                    if t["id"] == token_id and t.get("owner") == request.state.user["id"]
                ),
                None,
            )
            if not item:
                return JSONResponse({"error": "Token não encontrado."}, status_code=404)
            item["revoked_at"] = now_ms()
            if admin_store := getattr(app.state, "admin", None):
                await admin_store.audit(
                    request.state.user["name"], "Token pessoal revogado", token_id, icon="users"
                )
            return JSONResponse({k: v for k, v in item.items() if k != "hash"})

    def app_page_ctx(request: Request) -> dict[str, Any]:
        return page_ctx(request, "rail-hidden in-app") | {"scripts": ("hub-ux.js",)}

    mount_inbox(app, templates, page_ctx, gateway.client)
    mount_account(app, templates, page_ctx, icon)
    ux_track = mount_ux(
        app,
        gateway.registry,
        templates,
        page_ctx,
        app.state.ux,
        data_dir,
        lambda: public_status() or set(),
    )

    def track(request: Request, status_code: int) -> None:
        ux_track(request, status_code)
        allowed = getattr(request.state, "apps", None)
        insight.track(request, status_code, lambda a: allowed is None or a in allowed)

    app.state.ux_track = track
    mount_admin(
        app,
        templates,
        page_ctx,
        icon,
        gateway.registry,
        lambda: setattr(app.state, "roles", roles_for(data_dir)),
    )
    mount_connections(app, templates, page_ctx, icon, conns, admin, ObjectStore())
    mount_insight(app, templates, page_ctx, icon, insight, gateway.client)
    mount_ops(app, templates, page_ctx, icon, gateway.client, data_dir)
    mount_v1(app)
    mount_mcp(app)
    mount_oauth(app, templates)
    mount_gateway(app, gateway, templates, app_page_ctx, insight.observed)
    app.add_middleware(Cors, mcp_path=apicfg.mcp.path)
    return app
