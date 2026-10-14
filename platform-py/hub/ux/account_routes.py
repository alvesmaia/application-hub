"""Account: Settings (`/account/settings`), Personal tokens, Authorized apps and menu preferences."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from hub.auth import OWNED_ACTIONS
from hub.ux.shell import theme_of
from hub.ux.store import KINDS
from hub.ux.texts import LANG_NAMES, ago, t, tn

_GMT3 = timezone(timedelta(hours=-3))
_SCOPES = {
    "api:read": "acct.scope_api_read",
    "api:write": "acct.scope_api_write",
    "mcp": "acct.scope_mcp",
    "app-api": "acct.scope_app_api",
}


def safe_next(raw: str, default: str = "/") -> str:
    return raw if raw.startswith("/") and not raw.startswith(("//", "/\\")) else default


def short_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).astimezone(_GMT3).strftime("%d/%m")


@dataclass
class AccountCtx:
    """What the account pages need from `create_app` (`app.state.account_ctx`)."""

    templates: Jinja2Templates
    page_ctx: Callable[[Request], dict[str, Any]]
    icon: Callable[[str], str]

    def frame(self, request: Request, tab: str) -> dict[str, Any]:
        """Common context of the three tabs: Profile, Security and Access."""
        items = [("/account", "Perfil", "perfil"), ("/account/security", "Segurança", "seguranca")]
        items.append(("/account/access", "Acessos", "acessos"))
        return self.page_ctx(request) | {
            "tab": tab,
            "icon": self.icon,
            "tabs": [(h, label, tab == k) for h, label, k in items],
        }


def ctx(request: Request) -> AccountCtx:
    return cast(AccountCtx, request.app.state.account_ctx)


router = APIRouter()


@router.get("/account", response_class=HTMLResponse)
async def profile(request: Request, c: AccountCtx = Depends(ctx)) -> Response:
    lang, user = request.state.lang, request.state.user
    pref = request.app.state.ux.prefs(user["id"])
    themes = [("light", "shell.theme_light", "sun"), ("dark", "shell.theme_dark", "moon")]
    themes.append(("auto", "shell.theme_system", "themeAuto"))
    page = c.frame(request, "perfil") | {
        "email": user.get("email") or "—",
        "role": ", ".join(user["roles"]) or "—",
        "cur_theme": theme_of(request.state.theme)[0],
        "themes": [(v, t(k, lang), ic) for v, k, ic in themes],
        "lang_pref": pref.lang,
        "langs": [("", t("shell.language_auto", lang)), *LANG_NAMES.items()],
        "channels": [(k, pref.channel(k).in_app) for k in KINDS],
        "opt_out": pref.analytics_opt_out,
    }
    return c.templates.TemplateResponse(request, "account.html", page)


@router.get("/account/security", response_class=HTMLResponse)
async def security(request: Request, c: AccountCtx = Depends(ctx)) -> Response:
    return c.templates.TemplateResponse(request, "account.html", c.frame(request, "seguranca"))


@router.get("/account/access", response_class=HTMLResponse)
async def access(request: Request, msg: str = "", c: AccountCtx = Depends(ctx)) -> Response:
    lang, user, now = request.state.lang, request.state.user, int(time.time() * 1000)
    async with request.app.state.auth.locked() as d:
        mine = [x for x in d["tokens"] if x.get("owner") == user["id"] and x.get("revoked_at") is None]
    rows = [
        {
            "id": x["id"],
            "name": x["name"],
            "scopes": " ".join(x["actions"]),
            "created": short_date(x["created_at"]),
            "expires": short_date(x["expires_at"]) if x.get("expires_at") else t("acct.never", lang),
            "expired": bool(x.get("expires_at")) and x["expires_at"] <= now,
            "last": ago(x["last_used"], now, lang) if x.get("last_used") else "—",
        }
        for x in mine
    ]
    flash = {"revoked": "acct.revoked", "not_found": "acct.not_found"}.get(msg)
    page = c.frame(request, "acessos") | {
        "rows": rows,
        "scopes": [(s, t(k, lang)) for s, k in _SCOPES.items() if s in OWNED_ACTIONS],
        "expiries": [(str(n), tn("acct.days", n, lang)) for n in (30, 90, 180, 365)],
        "grants": await request.app.state.oauth.grants_view(user["id"], lang),
        "flash": t(flash, lang) if flash else "",
    }
    return c.templates.TemplateResponse(request, "account.html", page)


# Old addresses → the right tab of "My account".
@router.get("/account/settings")
async def old_settings() -> Response:
    return RedirectResponse("/account", 308)


@router.get("/account/tokens")
async def old_tokens() -> Response:
    return RedirectResponse("/account/access#tokens", 308)


@router.get("/account/authorized-apps")
async def old_apps(request: Request) -> Response:
    q = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(f"/account/access{q}#apps", 308)


@router.post("/account/authorized-apps/revoke")
async def revoke_authorized(request: Request) -> Response:
    form = parse_qs((await request.body()).decode("utf-8", "replace"))
    ok = await request.app.state.oauth.revoke_for_user(
        request.state.user["id"], form.get("grant", [""])[0], request.state.user["name"]
    )
    return RedirectResponse(f"/account/access?msg={'revoked' if ok else 'not_found'}#apps", 303)


@router.post("/account/prefs")
async def prefs_form(request: Request, next: str = "/") -> Response:
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
    msg = request.app.state.apply_prefs(request.state.user["id"], form.get("theme"), form.get("lang"))
    if msg:
        return Response(msg, status_code=400)
    return RedirectResponse(safe_next(next), 303)


def mount_account(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
) -> None:
    app.state.account_ctx = AccountCtx(templates, page_ctx, icon)
    app.include_router(router)
