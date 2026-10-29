"""Environment variables and Administration functions (`hub-admin::store`): rules, masking and operations."""

from __future__ import annotations

import re
from typing import Any

PERMISSIONS = (
    "Acessar aplicações",
    "Gerenciar usuários",
    "Editar configurações da plataforma",
    "Ver auditoria e logs",
    "Gerenciar variáveis de ambiente",
    "Aprovar solicitações",
)
LEVELS = ("Total", "Alto", "Padrão")
ADMIN_ROLE = "Administrador"
APP_ENVS = (("staging", "Staging"), ("producao", "Produção"))
KINDS = ("publica", "secreta", "connection_string")
KIND_LABEL = {"publica": "Pública", "secreta": "Secreta", "connection_string": "Connection string"}
RESERVED_ENV = ("HUB_APP_ID", "PORT", "HUB_SCRATCH_DIR", "TMPDIR")
INTERNAL_TOKEN_ENV = "HUB_INTERNAL_TOKEN"  # noqa: S105
PLATFORM_ENV = (INTERNAL_TOKEN_ENV, "HUB_STORAGE_TOKEN", "HUB_STORAGE_URL", "HUB_CONNECTIONS")
_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_DEFAULT_ROLES = (
    (ADMIN_ROLE, "Acesso completo à plataforma e a todas as aplicações", 1, "Total", (1, 1, 1, 1, 1, 1)),
    ("Product Owner", "Gerencia produtos, projetos e times digitais", 4, "Alto", (1, 0, 0, 1, 0, 1)),
    (
        "Tech Lead",
        "Acesso técnico a ambientes, deployments e configurações",
        3,
        "Alto",
        (1, 0, 1, 1, 1, 0),
    ),
    (
        "Colaborador",
        "Acesso às aplicações do dia a dia, sem áreas administrativas",
        20,
        "Padrão",
        (1, 0, 0, 0, 0, 0),
    ),
)
# Initial mockup variables (they apply to the three original apps; in production, all but the last one).
_DEFAULT_ENV = (
    ("API_BASE_URL", "https://api.platform.internal", "publica", "all", "producao"),
    ("AUTH_CLIENT_ID", "sk_live_7f3ac82e9b3f9a", "secreta", "all", "producao"),
    ("LOG_LEVEL", "info", "publica", "all", "producao"),
    ("FEATURE_FLAG_NEW_DASHBOARD", "true", "publica", ("fornecedores",), "producao"),
    (
        "NOTIFICATIONS_WEBHOOK_URL",
        "https://hooks.platform.internal/notify",
        "publica",
        ("servicos",),
        "producao",
    ),
    ("ASSET_SYNC_INTERVAL_MIN", "15", "publica", ("ativos",), "staging"),
)
_ORIGINAL_APPS = ("ativos", "fornecedores", "servicos")


# Dev password of the stack (compose) -> environment variable with the rotated password (`.prod.env`).
DEV_INFRA_PASSWORDS = {
    "APP_HUB_DB_PASSWORD": "hubpy-dev",
    "APP_HUB_APP_DB_PASSWORD": "hubpy-app-dev",
    "APP_HUB_OBJSTORE_PASSWORD": "hubpy-objstore-dev",
    # sample apps' own roles (hub/admin/demo_db.py); no dev password may contain another (plain substring replacement)
    "APP_HUB_APP_DB_PASSWORD_ATIVOS": "hubpy-ativos-dev",
    "APP_HUB_APP_DB_PASSWORD_FORNECEDORES": "hubpy-fornecedores-dev",
    "APP_HUB_APP_DB_PASSWORD_SERVICOS": "hubpy-servicos-dev",
    "APP_HUB_APP_DB_PASSWORD_COLETOR": "hubpy-coletor-dev",
}


class AdminError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def valid_env_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name))


def is_platform_env(name: str) -> bool:
    return any(name == p or name.startswith(p + "_") for p in PLATFORM_ENV)


def env_suffix(app: str) -> str:
    return app.upper().replace("-", "_")


def env_label(env: str) -> str:
    return dict(APP_ENVS).get(env, "?")


def is_secret(var: dict[str, Any]) -> bool:
    return bool(var.get("kind", "publica") != "publica")


def injected_name(var: dict[str, Any]) -> str:
    return str(var.get("inject_as") or var["name"])


def display_value(var: dict[str, Any]) -> str:
    kind, value = var.get("kind", "publica"), str(var["value"])
    if kind == "publica":
        return str(value)
    if kind == "connection_string" or len(value) <= 8:
        return "••••••••"
    return "••••••••" + value[-4:]


def masked(var: dict[str, Any]) -> dict[str, Any]:
    """Copy for listings, in the field order of the previous hub API; a secret's value never leaves here."""
    out: dict[str, Any] = {
        "name": var["name"],
        "value": display_value(var),
        "kind": var.get("kind", "publica"),
        "bindings": var.get("bindings", []),
    }
    if var.get("inject_as"):
        out["inject_as"] = var["inject_as"]
    out["managed"] = bool(var.get("managed"))
    return out


def bound_to(var: dict[str, Any], app: str, env: str | None = None) -> bool:
    return any(b["app"] == app and (env is None or b["env"] == env) for b in var.get("bindings", []))


def resolve(vars_: list[dict[str, Any]], app: str, env: str) -> list[dict[str, Any]]:
    """Variables that go into the `app` container under `env` (with values: for injection only)."""
    out = [
        {"name": injected_name(v), "value": v["value"], "secret": is_secret(v), "source": v["name"]}
        for v in vars_
        if bound_to(v, app, env)
    ]
    return sorted(out, key=lambda r: r["name"])


def seed(data: dict[str, Any], apps: list[str], with_env: bool = True) -> bool:
    """Initial data (variables and functions) when `admin.json` does not have them yet; variables only with the vault."""
    changed = False
    if with_env and not data.get("env") and not data.get("env_seeded"):
        for name, value, kind, scope, env in _DEFAULT_ENV:
            targets = [a for a in _ORIGINAL_APPS if a in apps] if scope == "all" else list(scope)
            bindings = sorted(({"app": a, "env": env} for a in targets), key=lambda b: (b["app"], b["env"]))
            data.setdefault("env", []).append(
                {"name": name, "value": value, "kind": kind, "bindings": bindings, "managed": False}
            )
        data["env_seeded"] = changed = True
    if not data.get("roles"):
        data["roles"] = [
            {"name": n, "description": d, "members": m, "level": lv, "permissions": [bool(x) for x in p]}
            for n, d, m, lv, p in _DEFAULT_ROLES
        ]
        changed = True
    data.setdefault("settings", {})
    data.setdefault("pending_restart", {})
    return changed
