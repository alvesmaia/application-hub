"""Shared state of the shell routes (settings, templates, stores, SSO) and the page context every screen renders with."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from hub.auth import ABSOLUTE_MS, COOKIE, AuthStore, entra
from hub.config import Settings
from hub.gateway.proxy import Gateway
from hub.insight.runtime import Insight
from hub.ux.inbox_routes import bell_of
from hub.ux.shell import can_open, i18n_for, nav_for, platform_palette, tabs_for, theme_of, visible_nav
from hub.ux.texts import t, tn, tr

THEME = "#0a2848"
# Discreet strip for staging mode (cookie `hub_env`), served together with the theme.
STAGING_BAR_CSS = (
    ".hub-staging-bar{display:flex;gap:8px;justify-content:center;align-items:center;padding:4px 16px;"
    "font-size:12px;background:var(--color-warning-soft);color:var(--color-text);"
    "border-bottom:1px solid var(--color-warning-shape)}.hub-staging-bar a{color:var(--color-text);font-weight:600}"
    # On app screens the workspace covers #app-root: the strip becomes a floating badge (F17).
    "html[data-hub-app] .hub-staging-bar{position:fixed;left:50%;bottom:12px;transform:translateX(-50%);"
    "z-index:var(--z-header);border:1px solid var(--color-warning-shape);border-radius:999px;"
    "box-shadow:0 4px 14px rgb(0 0 0/.15)}\n"
)
# hub-ux.js and the files split from it, in load order.
UX_SCRIPTS = ("hub-ux.js", "hub-ux-palette.js", "hub-ux-rail.js", "hub-ux-colfilter.js", "hub-ux-pages.js")
WEB_SCRIPTS = ("hub-i18n.js", "logshare.js", "admin.js", "ops.js", "ops-drawer.js", "insights.js", "ux.js")
SCRIPTS = (*UX_SCRIPTS, *WEB_SCRIPTS, "devdocs.js")


def initials(name: str) -> str:
    words = name.split()
    if not words:
        return "?"
    return words[0][:2].upper() if len(words) == 1 else (words[0][0] + words[-1][0]).upper()


def find_asset(settings: Settings, rel: str) -> Path | None:
    for base in settings.asset_dirs:
        p = (base / rel).resolve()
        if p.is_file() and p.is_relative_to(base.resolve()):
            return p
    return None


class WebCtx:
    """One per app (`app.state.web`); route handlers get it with `Depends(web)`."""

    def __init__(
        self,
        app: FastAPI,
        settings: Settings,
        templates: Jinja2Templates,
        gateway: Gateway,
        auth_store: AuthStore,
        insight: Insight,
        data_dir: Path,
    ) -> None:
        self.app, self.settings, self.templates, self.gateway = app, settings, templates, gateway
        self.auth_store, self.insight, self.data_dir = auth_store, insight, data_dir
        self.asset_cache: dict[
            str, tuple[float, int, int, bytes, str, str]
        ] = {}  # path → (checked, mtime_ns, size, body, type, etag)
        self.sso: entra.EntraConfig | None = None
        self.local_mode = "on"

    def load_sso(self, config: dict[str, Any] | None, vault: Any) -> None:
        try:
            self.sso, self.app.state.sso_reason = entra.load(config, vault), ""
        except entra.SsoError as e:
            self.sso, self.app.state.sso_reason = None, str(e)
            print(f"[hub] {e}", flush=True)
        self.local_mode = entra.local_login_mode(config) if self.sso else "on"
        self.app.state.sso, self.app.state.sso_local = self.sso, self.local_mode

    def _admin_settings(self) -> dict[str, Any] | None:
        try:
            return cast(
                dict[str, Any],
                json.loads((self.data_dir / "admin.json").read_text(encoding="utf-8")).get("settings", {}),
            )
        except (OSError, ValueError):
            return None

    def public_status(self) -> set[str] | None:
        """Apps of the public status page (`None` = public page turned off in Settings)."""
        cfg = self._admin_settings()
        if cfg is None:
            return None
        return set(cfg.get("status_public_apps", [])) if cfg.get("status_public") else None

    def brand_color(self) -> str:
        cfg = self._admin_settings()
        if cfg is None:
            return ""
        c = str(cfg.get("brand_color", "")).strip().lower()
        return c if re.fullmatch(r"#[0-9a-f]{6}", c) else ""

    def app_palette(self, request: Request, lang: str) -> str:
        """User's apps first, then the platform pages (Ctrl+K shortcut)."""
        allowed = getattr(request.state, "apps", None)
        apps = [
            {
                "title": e.name,
                "subtitle": e.display.get("description", ""),
                "url": f"/apps/{e.id}",
                "kind": t("shell.apps", lang),
                "score": 0.0,
            }
            for e in self.gateway.registry.entries()
            if allowed is None or e.id in allowed
        ]
        return json.dumps([*apps, *platform_palette(lang)], ensure_ascii=False, separators=(",", ":"))

    def page_ctx(self, request: Request, body_class: str = "rail-expanded") -> dict[str, Any]:
        principal = getattr(request.state, "user", {"name": "Visitante"})
        lang = getattr(request.state, "lang", "pt-BR")
        theme, color_scheme = theme_of(getattr(request.state, "theme", ""))
        user = {"name": principal["name"], "initials": initials(principal["name"])}
        unread, bell = bell_of(request) if hasattr(request.state, "user") else (0, [])
        rail_ctx, body_class, mode = _rail(request, body_class)
        perms = getattr(request.state, "permissions", [])
        return {
            "body_class": body_class,
            "rail_ctx": rail_ctx,
            "staging": request.cookies.get("hub_env") == "staging",
            "brand": self.settings.brand,
            "user": user,
            "perms": " ".join(perms),
            "nav": visible_nav(nav_for(lang), perms),
            "dev_menu": can_open("/developers", perms),  # user menu; same rule the rail item had
            "path": request.url.path,
            "tabs": tabs_for(request.url.path, request.query_params.get("tab", ""), perms),
            "manifest_href": _manifest_href(request.url.path),
            "i18n": i18n_for(lang),
            "palette": self.app_palette(request, lang),
            "scripts": SCRIPTS,
            "theme_color": THEME,
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
            "rail_label": f"{t('js.rail_menu', lang)}: {t(f'js.rail_{mode}', lang)}",
        }

    def app_page_ctx(self, request: Request) -> dict[str, Any]:
        return self.page_ctx(request, "rail-hidden in-app") | {"scripts": UX_SCRIPTS}

    def login_page(
        self, request: Request, next_url: str, error: str = "", email: str = "", status: int = 200
    ) -> Response:
        return self.templates.TemplateResponse(
            request,
            "login.html",
            {
                "brand": self.settings.brand,
                "next": next_url,
                "error": error,
                "email": email,
                "sso": self.sso is not None,
                "local": self.local_mode != "off",
                "lang": request.state.lang,
                "tr": lambda s: tr(s, request.state.lang),
            },
            status_code=status,
        )

    def password_page(
        self, request: Request, error: str = "", next_url: str = "/", status: int = 200
    ) -> Response:
        lang = request.state.lang
        ctx = {
            "brand": self.settings.brand,
            "error": error,
            "next": next_url,
            "lang": lang,
            "tr": lambda s: tr(s, lang),
        }
        return self.templates.TemplateResponse(
            request,
            "password.html",
            ctx | {"cancel": not request.state.user["must_change_password"]},
            status_code=status,
        )


def _rail(request: Request, body_class: str) -> tuple[str, str, str]:
    """(rail context, body classes, mode): the rail mode is stored separately inside and outside apps
    (cookie `hub_rail_<ctx>`, written by hub-ux.js), so the server already renders the page in the right mode."""
    in_app = "in-app" in body_class
    rail_ctx = "app" if in_app else "hub"
    chosen = request.cookies.get(f"hub_rail_{rail_ctx}", "")
    default = "hidden" if in_app else "expanded"
    mode = chosen if chosen in ("expanded", "collapsed", "hidden") else default
    body_class = " ".join([f"rail-{mode}", *[c for c in body_class.split() if not c.startswith("rail-")]])
    return rail_ctx, body_class, mode


def _manifest_href(path: str) -> str:
    m = re.match(r"^/apps/([a-z0-9-]+)(?:/|$)", path)
    return "/manifest.webmanifest" + (f"?start=/apps/{m[1]}" if m else "")


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


def public_base(request: Request) -> str:
    return os.getenv("HUB_PUBLIC_URL", "").strip().rstrip("/") or str(request.base_url).rstrip("/")


def sso_redirect_uri(request: Request) -> str:
    return f"{public_base(request)}/auth/entra/callback"


def web(request: Request) -> WebCtx:
    """FastAPI dependency: the shell state that `create_app` stored on the app."""
    return cast(WebCtx, request.app.state.web)
