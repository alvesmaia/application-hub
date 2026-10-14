"""Route permissions: roles, token scopes and what each admin/API area requires."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hub.models import RoleRecord

PERMISSIONS = (
    "acessar_aplicacoes",
    "gerenciar_usuarios",
    "editar_configuracoes",
    "ver_auditoria",
    "gerenciar_variaveis",
    "aprovar_solicitacoes",
)
OWNED_ACTIONS = ("api:read", "api:write", "mcp", "app-api")
TOKEN_ACTIONS = ("upload", "status", *OWNED_ACTIONS)


def user_view(u: dict[str, Any], now: int, locked: bool = False) -> dict[str, Any]:
    """User as the API shows it (fields and order of the `UserView` of the previous hub version; never the password hash)."""
    until = u.get("locked_until")
    until = until if until and until > now else None
    return {
        "id": u["id"],
        "email": u["email"],
        "name": u["name"],
        "roles": u["roles"],
        "active": u["active"],
        "must_change_password": bool(u.get("must_change_password")),
        "locked": locked or until is not None,
        "locked_until": until,
        "failed_attempts": u.get("failed_attempts", 0),
        "last_login": u.get("last_login"),
        "created_at": u["created_at"],
        "sso": bool(u.get("sso_subject")),
        "local_password": u.get("password_hash") is not None,
    }


def permissions(user: dict[str, Any], roles: list[dict[str, Any]]) -> tuple[list[str], list[str] | None]:
    flags = [False] * 6
    apps: list[str] | None = []
    for name in user["roles"]:
        role = next((r for r in roles if r["name"].lower() == name.lower()), None)
        if not role:
            continue
        admin = role["name"] == "Administrador"
        flags = [
            v
            or admin
            or bool(role.get("permissions", [])[i] if i < len(role.get("permissions", [])) else False)
            for i, v in enumerate(flags)
        ]
        if admin or role.get("permissions", [False])[0]:
            if admin or role.get("apps") is None:
                apps = None
            elif apps is not None:
                apps.extend(a for a in role["apps"] if a not in apps)
    return [p for i, p in enumerate(PERMISSIONS) if flags[i]], apps


def roles_for(data_dir: Path) -> list[dict[str, Any]]:
    defaults = [
        {"name": "Administrador", "permissions": [True] * 6, "apps": None},
        {"name": "Product Owner", "permissions": [True, False, False, True, False, True], "apps": None},
        {"name": "Tech Lead", "permissions": [True, False, True, True, True, False], "apps": None},
        {"name": "Colaborador", "permissions": [True, False, False, False, False, False], "apps": None},
    ]
    try:
        data = json.loads((data_dir / "admin.json").read_text(encoding="utf-8"))
        roles = data.get("roles", [])
        return [RoleRecord.model_validate(role).model_dump(exclude_none=True) for role in roles] or defaults
    except (OSError, ValueError, AttributeError):
        return defaults


def owned_surface(method: str, path: str, mcp_path: str = "/mcp") -> str | None:
    if path == "/api/v1" or path.startswith("/api/v1/"):
        return "api:write" if method not in ("GET", "HEAD", "OPTIONS") else "api:read"
    if path == mcp_path:
        return "mcp"
    parts = path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "apps" and parts[2] == "api":
        return "app-api"
    return None


def token_covers(token: dict[str, Any], scope: str) -> bool:
    actions = token.get("actions", [])
    if scope == "api:read":
        return "api:read" in actions or "api:write" in actions
    return token.get("owner") is not None and scope in actions


def token_action(method: str, path: str) -> str | None:
    if method == "POST" and path == "/api/ops/packages":
        return "upload"
    if method == "GET" and (
        path == "/api/ops/deployments"
        or (path.startswith("/api/ops/jobs/") and "/" not in path.removeprefix("/api/ops/jobs/"))
    ):
        return "status"
    return None


_ADMIN_REQUIRED = {
    "users": ("gerenciar_usuarios",),
    "roles": ("gerenciar_usuarios",),
    "audit": ("ver_auditoria",),
    "logs": ("ver_auditoria",),
    "env": ("gerenciar_variaveis", "editar_configuracoes"),
    "observability": ("editar_configuracoes", "ver_auditoria"),
    "analytics": ("editar_configuracoes", "ver_auditoria"),
    "usage": ("editar_configuracoes", "ver_auditoria"),
    "health": ("editar_configuracoes", "ver_auditoria"),
}


def admin_required(segment: str) -> tuple[str, ...]:
    """Permissions that open `/admin/<segment>` (any one is enough)."""
    return _ADMIN_REQUIRED.get(segment, ("editar_configuracoes",))


def api_required(method: str, segs: list[str]) -> tuple[str, ...]:
    """Permissions that open `/api/<segs>` (any one is enough); `segs` comes after `api`."""
    area, res = (segs[0] if segs else ""), (segs[1] if len(segs) > 1 else "")
    if area == "admin":
        if res == "env":
            safe = method in ("GET", "HEAD", "OPTIONS") and len(segs) == 2
            return ("gerenciar_variaveis", "editar_configuracoes") if safe else ("gerenciar_variaveis",)
        if res in ("roles", "users", "grants"):
            return ("gerenciar_usuarios",)
        if res in ("audit", "logs", "outbox"):
            return ("ver_auditoria",)
        safe = method in ("GET", "HEAD", "OPTIONS")
        if res in ("analytics", "analytics.csv", "usage", "usage.csv", "notifications") or (
            res in ("slos", "incidents") and safe
        ):
            return ("editar_configuracoes", "ver_auditoria")
        if res == "health" and (safe or (method == "POST" and segs[2:3] == ["check"])):
            return ("editar_configuracoes", "ver_auditoria")
    if area == "ops" and res == "rotate":
        return ("gerenciar_variaveis",)
    return ("editar_configuracoes",)
