"""Administração › Configurações (`/admin`) e Tokens de API (`/admin/tokens`, com clientes/concessões OAuth)."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError

from hub.admin.config import AdminError, is_secret
from hub.auth import TOKEN_ACTIONS, now_ms
from hub.ux.pager import paginate

_LOCAL = {"on": "Ativo para todos", "admins": "Só administradores", "off": "Desligado"}


def sso_status(request: Request) -> dict[str, Any]:
    """Estado do SSO para Configurações (só leitura; nunca o segredo do cliente)."""
    sso = getattr(request.app.state, "sso", None)
    local = _LOCAL[getattr(request.app.state, "sso_local", "on")]
    if not sso:
        return {"on": False, "local": local, "reason": getattr(request.app.state, "sso_reason", "")}
    return {"on": True, "tenant": sso.tenant, "groups": len(sso.role_map), "local": local}


LANGUAGES = (("pt-BR", "Português (Brasil)"), ("en", "English"))
_DAY = 86_400_000
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    platform_name: str = ""
    brand_color: str = ""
    status_public: bool = False
    status_public_apps: list[str] = []
    language: str
    timezone: str


class TokenIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    apps: list[str] = []
    envs: list[str] = []
    actions: list[str] = []
    expires_days: int | None = None


def _quirk(_ms: int, _now: int) -> str:
    # O Rust chama `relative(now, ts)` com os argumentos trocados e mostra sempre "agora mesmo" (paridade).
    return "agora mesmo"


def _kind(c: dict[str, Any]) -> str:
    if c.get("kind") == "cimd":
        return "Documento de metadados"
    m = c.get("token_endpoint_auth_method")
    if m in ("none", None):
        return "Registro dinâmico · cliente público (PKCE)"
    if m in ("client_secret_basic", "client_secret_post"):
        return "Registro dinâmico · com segredo"
    return f"Registro dinâmico · {m}"


def mount_tokens(
    app: FastAPI,
    api: APIRouter,
    render: Callable[..., Response],
    refs: Callable[[], list[dict[str, str]]],
    meta_json: Callable[[], str],
    actor_of: Callable[[Request], str],
) -> None:
    store: Any = app.state.admin
    auth: Any = app.state.auth

    # ---- Configurações ----------------------------------------------------------------------
    @api.get("/settings")
    async def get_settings() -> dict[str, Any]:
        return await _settings()

    async def _settings() -> dict[str, Any]:
        s = (await store.cfg.read()).get("settings", {})
        return {
            "platform_name": s.get("platform_name", ""),
            "brand_color": s.get("brand_color", ""),
            "status_public": bool(s.get("status_public", False)),
            "status_public_apps": list(s.get("status_public_apps", [])),
            "language": s.get("language", "pt-BR"),
            "timezone": s.get("timezone", "America/Sao_Paulo"),
        }

    @api.put("/settings")
    async def put_settings(request: Request) -> dict[str, Any]:
        try:
            v = SettingsIn.model_validate(await request.json())
        except (ValidationError, ValueError):
            raise AdminError("Dados inválidos: verifique os campos enviados.") from None
        name, color = v.platform_name.strip(), v.brand_color.strip().lower()
        if len(name) > 60 or any(ord(c) < 32 for c in name):
            raise AdminError("Nome da plataforma inválido (até 60 caracteres).")
        if v.language not in dict(LANGUAGES):
            raise AdminError("Idioma inválido.")
        if v.timezone != "America/Sao_Paulo":
            raise AdminError("Fuso horário inválido.")
        if color and not _COLOR.match(color):
            raise AdminError("Cor da marca inválida (use #rrggbb).")
        known = {a["id"] for a in refs()}
        if bad := next((a for a in v.status_public_apps if a not in known), None):
            raise AdminError(f"Aplicação desconhecida: {bad}.")
        out = {
            "platform_name": name,
            "brand_color": color,
            "status_public": v.status_public,
            "status_public_apps": sorted(set(v.status_public_apps)),
            "language": v.language,
            "timezone": v.timezone,
        }

        def fn(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:
            d["settings"] = out
            return (
                out,
                "Configurações da plataforma atualizadas",
                "Preferências globais",
                "settings",
                "platform",
            )

        return dict(await store.cfg.mutate(actor_of(request), fn))

    @api.get("/vault")
    async def vault_status() -> Any:
        """Estado do cofre (nada secreto: impressão digital da chave mestra e ids das chaves)."""
        v = store.vault
        if not v:
            return {"enabled": False}
        sealed = sum(1 for e in (await store.cfg.read()).get("env", []) if is_secret(e))
        return {"enabled": True, "kek_fingerprint": v.kek_fingerprint, "active_data_key": v.active_dek,
                "data_keys": v.dek_count, "encrypted_values": sealed, "audit_public_key": v.signing_public_key}  # fmt: skip

    @api.post("/vault/rotate-data-key")
    async def rotate(request: Request) -> Any:
        if not store.vault:
            return JSONResponse({"error": "Cofre não configurado."}, status_code=409)
        new = store.vault.rotate_data_key()

        def fn(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:  # regrava (recifra) o admin.json
            return (
                None,
                "Chave de dados do cofre rotacionada",
                f"Nova chave ativa {new}; segredos recifrados",
                "settings",
                "platform",
            )

        await store.cfg.mutate(actor_of(request), fn)
        return {"active_data_key": new}

    @app.get("/admin", response_class=HTMLResponse)
    async def settings_page(request: Request) -> Response:
        data = await store.cfg.read()
        s = await _settings()
        on = set(s["status_public_apps"])
        vault = None
        if store.vault:
            vault = {
                "kek": store.vault.kek_fingerprint,
                "dek": store.vault.active_dek,
                "deks": store.vault.dek_count,
                "sealed": sum(1 for e in data.get("env", []) if is_secret(e)),
            }
        sm = request.app.state.insight.analytics.summary(now_ms())
        names = {a["id"]: a["short"] for a in refs()}
        usage = {
            "mau": sm["mau"],
            "apps": sorted(((names.get(r[0], r[0]), r[3], r[4]) for r in sm["apps"]), key=lambda x: -x[2])[
                :5
            ],
            "zero": sm["searches_zero"],
            "searches": sm["searches"],
        }
        return render(
            request,
            "admin_settings.html",
            "Governança",
            "Configurações da plataforma",
            "Preferências globais, identidade e módulos.",
            meta=meta_json(),
            tab="Configurações",
            pname=s["platform_name"] or request.app.title,
            color=s["brand_color"],
            langs=LANGUAGES,
            lang_cur=s["language"],
            status_public=s["status_public"],
            apps=[{"id": a["id"], "short": a["short"], "on": a["id"] in on} for a in refs()],
            vault=vault,
            sso=sso_status(request),
            usage=usage,
        )

    # ---- Tokens de API ----------------------------------------------------------------------
    def view(t: dict[str, Any], now: int) -> dict[str, Any]:
        exp, rev = t.get("expires_at"), t.get("revoked_at")
        out = {k: t.get(k) for k in ("id", "name", "prefix", "apps", "envs", "actions", "created_by")}
        out |= {"created_at": t["created_at"], "expires_at": exp, "revoked_at": rev}
        out |= {"last_used": t.get("last_used"), "active": rev is None and (exp is None or exp > now)}
        if t.get("owner"):
            out["owner"] = t["owner"]
        return out

    @api.get("/tokens")
    async def list_tokens() -> list[dict[str, Any]]:
        now = now_ms()
        async with auth.locked() as d:
            return [view(t, now) for t in d["tokens"]]

    @api.post("/tokens")
    async def create_token(request: Request) -> JSONResponse:
        try:
            v = TokenIn.model_validate(await request.json())
        except (ValidationError, ValueError):
            raise AdminError("Dados de token inválidos.") from None
        if bad := next((a for a in v.apps if a not in {r["id"] for r in refs()}), None):
            raise AdminError(f"Aplicação desconhecida: {bad}.")
        if any(e not in ("staging", "producao") for e in v.envs) or any(
            a not in TOKEN_ACTIONS for a in v.actions
        ):
            raise AdminError("Dados de token inválidos.")
        try:
            secret, t = await auth.create_token(
                request.state.user, v.name, v.apps, v.actions, v.expires_days, envs=v.envs
            )
        except ValueError as e:
            raise AdminError(str(e)) from None
        scope = lambda x: ", ".join(x) or "todos"  # noqa: E731
        await store.audit(
            actor_of(request),
            "Token de API criado",
            f"{t['name']} ({t['prefix']}…) · apps: {scope(t['apps'])} · ambientes: {scope(t['envs'])}"
            f" · ações: {', '.join(t['actions'])}",
            icon="settings",
        )
        return JSONResponse({"token": secret, "item": view(t, now_ms())}, status_code=201)

    @api.post("/tokens/{token_id}/revoke")
    async def revoke_token(request: Request, token_id: str) -> JSONResponse:
        async with auth.locked(True) as d:
            t = next((x for x in d["tokens"] if x["id"] == token_id), None)
            if not t:
                raise AdminError("Token não encontrado.", 404)
            t["revoked_at"] = t.get("revoked_at") or now_ms()
            item = view(t, now_ms())
        await store.audit(
            actor_of(request), "Token de API revogado", f"{t['name']} ({t['prefix']}…)", icon="settings"
        )
        return JSONResponse(item)

    @api.delete("/tokens/{token_id}")
    async def delete_token(request: Request, token_id: str) -> Response:
        async with auth.locked(True) as d:
            t = next((x for x in d["tokens"] if x["id"] == token_id), None)
            if not t:
                raise AdminError("Token não encontrado.", 404)
            live = t.get("revoked_at") is None  # excluir um token ativo = revogar e remover numa só ação
            d["tokens"].remove(t)
        await store.audit(
            actor_of(request),
            "Token de API excluído",
            f"{t['name']} ({t['prefix']}…)" + (" · revogado ao excluir" if live else ""),
            icon="trash",
        )
        return Response(status_code=204)

    @app.get("/admin/tokens", response_class=HTMLResponse)
    async def tokens_page(request: Request) -> Response:
        now = now_ms()
        async with auth.locked() as d:
            tokens = sorted(d["tokens"], key=lambda t: -t["created_at"])
        rows = []
        for t in tokens:
            v = view(t, now)
            status, cls = (
                ("Revogado", "status-pill status-draft")
                if t.get("revoked_at") is not None
                else ("Ativo", "status-pill status-active")
                if v["active"]
                else ("Vencido", "status-pill status-maintenance")
            )
            exp = t.get("expires_at")
            rows.append(
                {
                    "id": t["id"],
                    "name": t["name"],
                    "prefix": f"{t['prefix']}…",
                    "scope": f"Apps: {', '.join(t['apps']) or 'todos'} · Ambientes: {', '.join(t['envs']) or 'todos'}",
                    "actions": ", ".join(t["actions"]),
                    "created": f"{t['created_by']} · {_quirk(t['created_at'], now)}",
                    "expires": (
                        "—"
                        if exp is None
                        else f"em {-(-(exp - now) // _DAY)} dias"
                        if exp > now
                        else "vencido"
                    ),
                    "last": _quirk(t["last_used"], now) if t.get("last_used") else "Nunca",
                    "active": v["active"],
                    "status": status,
                    "cls": cls,
                }
            )
        oauth = request.app.state.oauth
        users = {u["id"]: u["email"] for u in (await _users())}
        clients = []
        for c in await oauth.clients():
            blocked = bool(c.get("disabled"))
            status, cls = (
                ("Bloqueado", "status-pill status-draft")
                if blocked
                else ("Em uso", "status-pill status-active")
                if c.get("used")
                else ("Nunca usado", "status-pill status-maintenance")
            )
            clients.append(
                {
                    "id": c.get("client_id", ""),
                    "name": c.get("client_name", ""),
                    "kind": _kind(c),
                    "hosts": ", ".join(
                        u.split("/")[2] if u.count("/") >= 2 else u for u in c.get("redirect_uris", [])
                    ),
                    "created": _quirk(c.get("created_at", 0), now),
                    "status": status,
                    "cls": cls,
                    "action": "/admin/oauth/unblock-client" if blocked else "/admin/oauth/block-client",
                    "label": "Desbloquear" if blocked else "Bloquear",
                }
            )
        grants = [
            {
                "id": g.get("grant_id", ""),
                "client": g.get("client_name", ""),
                "user": users.get(g.get("user", ""), g.get("user", "")),
                "scopes": " ".join(g.get("scopes", [])),
                "created": _quirk(g.get("created_at", 0), now),
                "last": _quirk(g.get("last_used", 0), now),
            }
            for g in await oauth.grants()
        ]
        rows, pager = paginate(request, rows)
        return render(
            request,
            "admin_tokens.html",
            "Acesso",
            "Credenciais",
            "Credenciais para pipelines de CI/CD e integrações. O token aparece uma única vez.",
            rows=rows,
            clients=clients,
            grants=grants,
            oauth_on=request.app.state.apicfg.oauth_on,
            meta=meta_json_users(),
            pager=pager,
        )

    async def _users() -> list[dict[str, Any]]:
        async with auth.locked() as d:
            return [dict(u) for u in d["users"]]

    def meta_json_users() -> str:
        import json

        roles = [r["name"] for r in app.state.roles]
        apps = [{"id": a["id"], "name": a["name"]} for a in refs()]
        return json.dumps(
            {"apps": apps, "roles": roles}, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
