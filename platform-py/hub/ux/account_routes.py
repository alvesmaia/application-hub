"""Conta: Configurações (`/account/settings`), Tokens pessoais, Apps autorizados, preferências do menu
e a página de aprovação de ação do agente (credencial: pede a senha de novo, sem JavaScript)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from hub.auth import OWNED_ACTIONS
from hub.ux.inbox import InboxError, InboxStore, targets
from hub.ux.inbox_routes import DecideIn, who_of
from hub.ux.shell import theme_of
from hub.ux.store import KINDS
from hub.ux.texts import LANG_NAMES, ago, t, tn, tr

_GMT3 = timezone(timedelta(hours=-3))
_SCOPES = {
    "api:read": "acct.scope_api_read",
    "api:write": "acct.scope_api_write",
    "mcp": "acct.scope_mcp",
    "app-api": "acct.scope_app_api",
}
_AGENT_CSP = (
    "default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self'; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)


def safe_next(raw: str, default: str = "/") -> str:
    return raw if raw.startswith("/") and not raw.startswith(("//", "/\\")) else default


def short_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).astimezone(_GMT3).strftime("%d/%m")


def mount_account(
    app: FastAPI,
    templates: Jinja2Templates,
    page_ctx: Callable[[Request], dict[str, Any]],
    icon: Callable[[str], str],
) -> None:
    inbox = cast(InboxStore, app.state.inbox)

    def frame(request: Request, tab: str) -> dict[str, Any]:
        """Contexto comum das três abas: Perfil, Segurança e Acessos."""
        items = [("/account", "Perfil", "perfil"), ("/account/security", "Segurança", "seguranca")]
        items.append(("/account/access", "Acessos", "acessos"))
        return page_ctx(request) | {
            "tab": tab,
            "icon": icon,
            "tabs": [(h, label, tab == k) for h, label, k in items],
        }

    @app.get("/account", response_class=HTMLResponse)
    async def profile(request: Request) -> Response:
        lang, user = request.state.lang, request.state.user
        pref = app.state.ux.prefs(user["id"])
        themes = [("light", "shell.theme_light", "sun"), ("dark", "shell.theme_dark", "moon")]
        themes.append(("auto", "shell.theme_system", "themeAuto"))
        ctx = frame(request, "perfil") | {
            "email": user.get("email") or "—",
            "role": ", ".join(user["roles"]) or "—",
            "cur_theme": theme_of(request.state.theme)[0],
            "themes": [(v, t(k, lang), ic) for v, k, ic in themes],
            "lang_pref": pref.lang,
            "langs": [("", t("shell.language_auto", lang)), *LANG_NAMES.items()],
            "channels": [(k, pref.channel(k).in_app) for k in KINDS],
            "opt_out": pref.analytics_opt_out,
        }
        return templates.TemplateResponse(request, "account.html", ctx)

    @app.get("/account/security", response_class=HTMLResponse)
    async def security(request: Request) -> Response:
        return templates.TemplateResponse(request, "account.html", frame(request, "seguranca"))

    @app.get("/account/access", response_class=HTMLResponse)
    async def access(request: Request, msg: str = "") -> Response:
        lang, user, now = request.state.lang, request.state.user, int(time.time() * 1000)
        async with app.state.auth.locked() as d:
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
        ctx = frame(request, "acessos") | {
            "rows": rows,
            "scopes": [(s, t(k, lang)) for s, k in _SCOPES.items() if s in OWNED_ACTIONS],
            "expiries": [(str(n), tn("acct.days", n, lang)) for n in (30, 90, 180, 365)],
            "grants": await app.state.oauth.grants_view(user["id"], lang),
            "flash": t(flash, lang) if flash else "",
        }
        return templates.TemplateResponse(request, "account.html", ctx)

    # Endereços antigos → aba certa de "Minha conta".
    @app.get("/account/settings")
    async def old_settings() -> Response:
        return RedirectResponse("/account", 308)

    @app.get("/account/tokens")
    async def old_tokens() -> Response:
        return RedirectResponse("/account/access#tokens", 308)

    @app.get("/account/authorized-apps")
    async def old_apps(request: Request) -> Response:
        q = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/account/access{q}#apps", 308)

    @app.post("/account/authorized-apps/revoke")
    async def revoke_authorized(request: Request) -> Response:
        form = parse_qs((await request.body()).decode("utf-8", "replace"))
        ok = await app.state.oauth.revoke_for_user(
            request.state.user["id"], form.get("grant", [""])[0], request.state.user["name"]
        )
        return RedirectResponse(f"/account/access?msg={'revoked' if ok else 'not_found'}#apps", 303)

    @app.post("/account/prefs")
    async def prefs_form(request: Request, next: str = "/") -> Response:
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
        msg = app.state.apply_prefs(request.state.user["id"], form.get("theme"), form.get("lang"))
        if msg:
            return Response(msg, status_code=400)
        return RedirectResponse(safe_next(next), 303)

    # ---- aprovação de ação do agente ----------------------------------------------------------------
    def agent_page(request: Request, item_id: int, alert: str = "", status: int = 200) -> Response:
        who = who_of(request)
        it = inbox.item(item_id)
        if not who.session:
            return agent_message(
                request,
                403,
                "Só pela sua sessão",
                "Ações do agente só são aprovadas pela sessão do navegador do dono do token.",
            )
        if not it or it["kind"] != "agent":
            return agent_message(
                request, 404, "Pedido não encontrado", "Este pedido não existe ou não é seu."
            )
        if not targets(it, who):
            return agent_message(
                request, 404, "Pedido não encontrado", "Este pedido não existe ou não é seu."
            )
        entry = next(
            (
                e
                for e in inbox.entries(who, "pt-BR", lambda _k: True, lambda a: a)
                if e["id"] == f"i:{item_id}"
            ),
            None,
        )
        ap = entry["approval"] if entry else it["approval"]
        left = max(it["agent"]["expires_at"] - int(time.time() * 1000), 0) // 1000
        ctx = {
            "brand": app.title,
            "lang": request.state.lang,
            "tr": lambda s: tr(s, request.state.lang),
            "it": it,
            "agent": it["agent"],
            "ap": ap,
            "pending": ap["status"] == "pending" and bool(entry and entry["approval"]["can_decide"]),
            "left": left,
            "alert": alert,
            "id": item_id,
        }
        return agent_response(
            templates.TemplateResponse(request, "agent_approval.html", ctx, status_code=status)
        )

    def agent_response(r: Response) -> Response:
        r.headers.update(
            {
                "content-security-policy": _AGENT_CSP,
                "x-frame-options": "DENY",
                "referrer-policy": "no-referrer",
                "cache-control": "no-store",
            }
        )
        return r

    def agent_message(request: Request, status: int, title: str, msg: str) -> Response:
        ctx = {
            "brand": app.title,
            "lang": request.state.lang,
            "tr": lambda s: tr(s, request.state.lang),
            "title": title,
            "msg": msg,
        }
        return agent_response(
            templates.TemplateResponse(request, "agent_message.html", ctx, status_code=status)
        )

    @app.get("/account/agent-approval/{item}", response_class=HTMLResponse)
    async def agent_show(request: Request, item: str) -> Response:
        n = item.removeprefix("i:")
        return agent_page(request, int(n) if n.isdigit() else -1)

    @app.post("/account/agent-approval/{item}")
    async def agent_decide(request: Request, item: str) -> Response:
        n = int(item.removeprefix("i:")) if item.removeprefix("i:").isdigit() else -1
        who, it = who_of(request), inbox.item(n)
        if not who.session or not it or it["kind"] != "agent":
            return agent_page(request, n)
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode("utf-8", "replace")).items()}
        decision, comment = form.get("decision", ""), form.get("comment", "")
        step_up = ""
        if decision == "approve":
            # confirmação dupla na tela (botão + modal), só por este formulário com a sessão do navegador:
            # a API (token do agente) continua sem poder aprovar
            step_up = inbox.stepup_issue(who.id, n, it["agent"]["args_hash"])
        elif decision != "reject":
            return agent_page(request, n, "Decisão inválida.", 400)
        try:
            await app.state.inbox_decide(
                request, who, n, DecideIn(decision=decision, comment=comment, step_up=step_up)
            )
        except InboxError as e:
            return agent_page(request, n, e.message, e.status)
        return RedirectResponse(safe_next(form.get("next", ""), "/inbox?filter=approvals"), 303)
