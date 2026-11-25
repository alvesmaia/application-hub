"""Administration › Settings (`/admin`); `/admin/tokens` moved to My account › Access."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, ValidationError

from hub.admin.adminctx import AdminCtx, actor_of, ctx
from hub.admin.config import AdminError, is_secret
from hub.auth import now_ms

_LOCAL = {"on": "Ativo para todos", "admins": "Só administradores", "off": "Desligado"}

api = APIRouter()
pages = APIRouter()


def sso_status(request: Request) -> dict[str, Any]:
    """SSO status for Settings (read-only; never the client secret)."""
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
    catalog_hidden_apps: list[str] = []
    language: str
    timezone: str


# ---- Settings ----------------------------------------------------------------------
async def _settings(c: AdminCtx) -> dict[str, Any]:
    s = (await c.store.cfg.read()).get("settings", {})
    return {
        "platform_name": s.get("platform_name", ""),
        "brand_color": s.get("brand_color", ""),
        "status_public": bool(s.get("status_public", False)),
        "status_public_apps": list(s.get("status_public_apps", [])),
        "catalog_hidden_apps": list(s.get("catalog_hidden_apps", [])),
        "language": s.get("language", ""),  # empty = automatic (the browser's language)
        "timezone": s.get("timezone", "America/Sao_Paulo"),
    }


@api.get("/settings")
async def get_settings(c: AdminCtx = Depends(ctx)) -> dict[str, Any]:
    return await _settings(c)


def _check_settings(v: SettingsIn, name: str, color: str, known: set[str]) -> None:
    if len(name) > 60 or any(ord(ch) < 32 for ch in name):
        raise AdminError("Nome da plataforma inválido (até 60 caracteres).")
    if v.language and v.language not in dict(LANGUAGES):
        raise AdminError("Idioma inválido.")
    if v.timezone != "America/Sao_Paulo":
        raise AdminError("Fuso horário inválido.")
    if color and not _COLOR.match(color):
        raise AdminError("Cor da marca inválida (use #rrggbb).")
    if bad := next((a for a in [*v.status_public_apps, *v.catalog_hidden_apps] if a not in known), None):
        raise AdminError(f"Aplicação desconhecida: {bad}.")


@api.put("/settings")
async def put_settings(request: Request, c: AdminCtx = Depends(ctx)) -> dict[str, Any]:
    try:
        v = SettingsIn.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise AdminError("Dados inválidos: verifique os campos enviados.") from None
    name, color = v.platform_name.strip(), v.brand_color.strip().lower()
    _check_settings(v, name, color, {a["id"] for a in c.refs()})
    out = {
        "platform_name": name,
        "brand_color": color,
        "status_public": v.status_public,
        "status_public_apps": sorted(set(v.status_public_apps)),
        "catalog_hidden_apps": sorted(set(v.catalog_hidden_apps)),
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

    return dict(await c.store.cfg.mutate(actor_of(request), fn))


@api.get("/vault")
async def vault_status(c: AdminCtx = Depends(ctx)) -> Any:
    """Vault status (nothing secret: master key fingerprint and key ids)."""
    v = c.store.vault
    if not v:
        return {"enabled": False}
    sealed = sum(1 for e in (await c.store.cfg.read()).get("env", []) if is_secret(e))
    return {"enabled": True, "kek_fingerprint": v.kek_fingerprint, "active_data_key": v.active_dek,
            "data_keys": v.dek_count, "encrypted_values": sealed, "audit_public_key": v.signing_public_key}  # fmt: skip


@api.post("/vault/rotate-data-key")
async def rotate(request: Request, c: AdminCtx = Depends(ctx)) -> Any:
    if not c.store.vault:
        return JSONResponse({"error": "Cofre não configurado."}, status_code=409)
    new = c.store.vault.rotate_data_key()

    def fn(d: dict[str, Any]) -> tuple[Any, str, str, str, str]:  # regrava (recifra) o admin.json
        return (
            None,
            "Chave de dados do cofre rotacionada",
            f"Nova chave ativa {new}; segredos recifrados",
            "settings",
            "platform",
        )

    await c.store.cfg.mutate(actor_of(request), fn)
    return {"active_data_key": new}


def _usage(request: Request, c: AdminCtx) -> dict[str, Any]:
    sm = request.app.state.insight.analytics.summary(now_ms())
    names = {a["id"]: a["short"] for a in c.refs()}
    return {
        "mau": sm["mau"],
        "apps": sorted(((names.get(r[0], r[0]), r[3], r[4]) for r in sm["apps"]), key=lambda x: -x[2])[:5],
        "zero": sm["searches_zero"],
        "searches": sm["searches"],
    }


@pages.get("/admin", response_class=HTMLResponse)
async def settings_page(request: Request, c: AdminCtx = Depends(ctx)) -> Response:
    data = await c.store.cfg.read()
    s = await _settings(c)
    on, hidden = set(s["status_public_apps"]), set(s["catalog_hidden_apps"])
    vault = None
    if c.store.vault:
        vault = {
            "kek": c.store.vault.kek_fingerprint,
            "dek": c.store.vault.active_dek,
            "deks": c.store.vault.dek_count,
            "sealed": sum(1 for e in data.get("env", []) if is_secret(e)),
        }
    usage = _usage(request, c)
    return c.render(
        request,
        "admin_settings.html",
        "Governança",
        "Configurações da plataforma",
        "Preferências globais, identidade e módulos.",
        meta=c.meta_json(),
        tab="Configurações",
        pname=s["platform_name"] or request.app.title,
        color=s["brand_color"],
        langs=(("", "Automático (idioma do navegador)"), *LANGUAGES),
        lang_cur=s["language"],
        status_public=s["status_public"],
        apps=[
            {"id": a["id"], "short": a["short"], "on": a["id"] in on, "hidden": a["id"] in hidden}
            for a in c.refs()
        ],
        vault=vault,
        sso=sso_status(request),
        usage=usage,
    )


# The platform-wide token page is gone: every credential belongs to a person (My account › Access).
@pages.get("/admin/tokens")
async def tokens_moved() -> RedirectResponse:
    return RedirectResponse("/account/access", status_code=308)
