"""Usuários locais, sessões, tokens de API e permissões de rota (formato compatível com a versão anterior do hub)."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import secrets
import time
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from argon2 import PasswordHasher, exceptions
from argon2.low_level import Type
from fastapi import Request

from hub.models import AuthDocument, RoleRecord
from hub.state import SessionLimits, SessionRow, StateDb

COOKIE = "hub_session"
SSO_COOKIE = "hub_sso"
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
PH = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16, type=Type.ID)


def positive_env(name: str, default: int, cap: int) -> int:
    try:
        return min(cap, max(1, int(os.getenv(name, str(default)).strip())))
    except ValueError:
        return default


IDLE_MS = positive_env("HUB_SESSION_IDLE_MIN", 30, 24 * 60) * 60_000
ABSOLUTE_MS = positive_env("HUB_SESSION_ABSOLUTE_HOURS", 12, 24 * 365) * 3_600_000
MAX_SESSIONS = positive_env("HUB_MAX_SESSIONS_PER_USER", 10, 1000)
DUMMY_HASH = PH.hash("senha-que-nunca-confere")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def policy(password: str) -> str | None:
    n = len(password)
    if n < 10:
        return "A senha deve ter pelo menos 10 caracteres."
    if n > 128:
        return "A senha deve ter no máximo 128 caracteres."
    if any(unicodedata.category(c) == "Cc" for c in password):
        return "A senha não pode ter caracteres de controle."
    if (
        sum(
            (
                any(c.islower() for c in password),
                any(c.isupper() for c in password),
                any(c.isascii() and c.isdigit() for c in password),
                any(not c.isalnum() for c in password),
            )
        )
        < 2
    ):
        return "Combine letras com números ou símbolos."
    return None


def verify(password: str, encoded: str | None) -> bool:
    try:
        return encoded is not None and PH.verify(encoded, password)
    except (exceptions.VerifyMismatchError, exceptions.VerificationError, ValueError):
        return False


def _user_login(users: list[dict[str, Any]], login: str) -> dict[str, Any] | None:
    key = login.strip().lower()
    found = [u for u in users if u["email"] == key]
    if not found and "@" not in key:
        found = [u for u in users if u["email"].split("@", 1)[0] == key]
    demos = [u for u in found if u["email"].endswith("@demo.local")]
    return demos[0] if len(demos) == 1 else found[0] if len(found) == 1 else None


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    local, sep, domain = email.partition("@")
    if len(email) > 254 or not sep or not local or not domain or domain.startswith(".") or "." not in domain:
        raise ValueError("Informe um e-mail válido.")
    if any(c.isspace() or unicodedata.category(c) == "Cc" or c in "<>" for c in email):
        raise ValueError("Informe um e-mail válido.")
    return email


@dataclass
class AuthStore:
    path: Path
    db: StateDb | None = None
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        if not self.path.exists():
            self.path.write_text('{"users":[],"sessions":[],"tokens":[],"next_id":0}', encoding="utf-8")

    @asynccontextmanager
    async def locked(self, write: bool = False) -> AsyncIterator[dict[str, Any]]:
        async with self._lock:
            if self.db:
                async with self.db.file_lock("auth.json", write=write):
                    data = self._read()
                    yield data
            else:
                data = self._read()
                yield data
            if write:
                data.pop("dropped", None)
                if self.db:
                    data["sessions"] = []
                for user in data["users"]:
                    for key in ("password_hash", "sso_subject", "sso_issuer"):
                        if user.get(key) is None:
                            user.pop(key, None)
                for token in data["tokens"]:
                    if token.get("owner") is None:
                        token.pop("owner", None)
                tmp = self.path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                with suppress(OSError):
                    tmp.chmod(0o600)
                os.replace(tmp, self.path)

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        data = {"users": [], "sessions": [], "tokens": [], "next_id": 0, **data}
        AuthDocument.model_validate(data)
        return data

    def _id(self, d: dict[str, Any], prefix: str) -> str:
        d["next_id"] += 1
        return f"{prefix}{d['next_id']}{secrets.token_hex(4)}"

    async def bootstrap(self) -> None:
        async with self.locked(True) as d:
            if os.getenv("HUB_DEMO_USERS", "").lower() in ("1", "true", "yes", "on"):
                shared = os.getenv("HUB_DEMO_PASSWORD") or None
                if shared and policy(shared):
                    shared = None
                demos = (
                    (
                        "admin@demo.local",
                        "Administrador",
                        "Administrador",
                        shared or "admin",
                    ),
                    (
                        "operador@demo.local",
                        "Operador",
                        "Tech Lead",
                        shared or "operador",
                    ),
                    (
                        "leitor@demo.local",
                        "Leitor",
                        "Colaborador",
                        shared or "leitor",
                    ),
                )
                for email, name, role, password in demos:
                    u = next((x for x in d["users"] if x["email"] == email), None)
                    if u is None:
                        u = self._new_user(d, email, name, role, password, False)
                        d["users"].append(u)
                    else:
                        u.update(name=name, password_hash=PH.hash(password), must_change_password=False)
                return
            if d["users"]:
                return
            email = os.getenv("HUB_BOOTSTRAP_ADMIN_EMAIL", "admin@hub.local").strip().lower()
            configured = os.getenv("HUB_BOOTSTRAP_ADMIN_PASSWORD")
            generated = not configured
            alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!@#%+="
            password = configured or ""
            while not configured and policy(password):
                password = "".join(secrets.choice(alphabet) for _ in range(20))
            if err := policy(password):
                raise ValueError(f"HUB_BOOTSTRAP_ADMIN_PASSWORD: {err}")
            u = self._new_user(d, email, "Administrador", "Administrador", password, generated)
            d["users"].append(u)
            if generated:
                f = self.path.parent / "bootstrap-admin.txt"
                f.write_text(
                    f"Administrador inicial da plataforma\nE-mail: {email}\nSenha: {password}\n\nTroque a senha no primeiro acesso.\n",
                    encoding="utf-8",
                )
                f.chmod(0o600)

    def _new_user(
        self, d: dict[str, Any], email: str, name: str, role: str, password: str, change: bool
    ) -> dict[str, Any]:
        n = now_ms()
        return {
            "id": self._id(d, "u"),
            "email": email,
            "name": name,
            "roles": [role],
            "active": True,
            "password_hash": PH.hash(password),
            "must_change_password": change,
            "created_at": n,
            "updated_at": n,
            "last_login": None,
            "failed_attempts": 0,
            "locked_until": None,
            "sso_subject": None,
            "sso_issuer": None,
        }

    async def authenticate(self, login: str, password: str) -> dict[str, Any] | None:
        async with self.locked(True) as d:
            user = _user_login(d["users"], login)
            encoded = user.get("password_hash") if user else None
            if not verify(password, encoded) or not user or not user["active"]:
                if encoded is None:
                    verify(password, DUMMY_HASH)
                return None
            user["last_login"] = now_ms()
            user["failed_attempts"] = 0
            user["locked_until"] = None
            return user.copy()

    async def sso_login(
        self, issuer: str, subject: str, email: str, name: str, roles: list[str] | None, default_role: str
    ) -> tuple[dict[str, Any] | None, bool]:
        """Usuário do SSO: pelo vínculo (emissor+sujeito), senão pelo e-mail (vincula), senão cria (JIT).
        `roles` (do mapeamento de grupos) substitui as funções quando não vazio. Desativado: (None, False)."""
        async with self.locked(True) as d:
            user = next(
                (u for u in d["users"] if u.get("sso_issuer") == issuer and u.get("sso_subject") == subject),
                None,
            ) or next((u for u in d["users"] if u["email"] == email), None)
            created = user is None
            if user is None:
                n = now_ms()
                user = {
                    "id": self._id(d, "u"),
                    "email": email,
                    "name": name[:80] or email,
                    "roles": [default_role],
                    "active": True,
                    "must_change_password": False,
                    "created_at": n,
                    "updated_at": n,
                    "last_login": None,
                    "failed_attempts": 0,
                    "locked_until": None,
                }
                d["users"].append(user)
            if not user["active"]:
                return None, False
            user.update(sso_issuer=issuer, sso_subject=subject, last_login=now_ms(), updated_at=now_ms())
            if roles:
                user["roles"] = roles
            return user.copy(), created

    async def session_new(
        self, user: dict[str, Any], previous: str | None = None, method: str = "password"
    ) -> str:
        token = secrets.token_urlsafe(32)
        n, h = now_ms(), sha(token)
        if self.db:
            await self.db.session_create(
                SessionRow(h, user["id"], n, n, method),
                SessionLimits(IDLE_MS, ABSOLUTE_MS, MAX_SESSIONS),
                sha(previous) if previous else None,
            )
            return token
        async with self.locked(True) as d:
            d["sessions"] = [
                s
                for s in d["sessions"]
                if s["id_hash"] != (sha(previous) if previous else "")
                and n - s["last_seen"] < IDLE_MS
                and n - s["created_at"] < ABSOLUTE_MS
            ]
            theirs = sorted(
                (s for s in d["sessions"] if s["user_id"] == user["id"]), key=lambda s: s["last_seen"]
            )
            while len(theirs) >= MAX_SESSIONS:
                d["sessions"].remove(theirs.pop(0))
            d["sessions"].append(
                {"id_hash": h, "user_id": user["id"], "created_at": n, "last_seen": n, "method": method}
            )
        return token

    async def session(self, token: str) -> dict[str, Any] | None:
        h, n = sha(token), now_ms()
        if self.db:
            s = await self.db.session_get(h)
            if not s:
                return None
            if n - s.last_seen >= IDLE_MS or n - s.created_at >= ABSOLUTE_MS:
                await self.db.session_delete(h)
                return None
            if n - s.last_seen >= 60_000:
                await self.db.session_touch(h, n, 60_000)
            user_id = s.user_id
        else:
            async with self.locked(True) as d:
                s = next((x for x in d["sessions"] if x["id_hash"] == h), None)
                if not s:
                    return None
                if n - s["last_seen"] >= IDLE_MS or n - s["created_at"] >= ABSOLUTE_MS:
                    d["sessions"].remove(s)
                    return None
                s["last_seen"] = n
                user_id = s["user_id"]
        async with self.locked() as d:
            user = next((u.copy() for u in d["users"] if u["id"] == user_id and u["active"]), None)
        return user

    async def logout(self, token: str) -> None:
        h = sha(token)
        if self.db:
            await self.db.session_delete(h)
        else:
            async with self.locked(True) as d:
                d["sessions"] = [s for s in d["sessions"] if s["id_hash"] != h]

    async def create_user(
        self, email: str, name: str, roles: list[str], password: str, must_change: bool = True
    ) -> dict[str, Any]:
        email = normalize_email(email)
        name = name.strip()
        roles = list(dict.fromkeys(r.strip() for r in roles if r.strip()))
        if not name or len(name) > 80 or any(unicodedata.category(c) == "Cc" for c in name):
            raise ValueError("Informe o nome (até 80 caracteres).")
        if not roles or len(roles) > 10 or any(len(r) > 60 for r in roles):
            raise ValueError("Escolha funções válidas.")
        if err := policy(password):
            raise ValueError(err)
        async with self.locked(True) as d:
            if any(u["email"] == email for u in d["users"]):
                raise ValueError("Já existe um usuário com este e-mail.")
            user = self._new_user(d, email, name, roles[0], password, must_change)
            user["roles"] = roles
            d["users"].append(user)
            return user.copy()

    async def update_user(
        self,
        user_id: str,
        *,
        name: str | None = None,
        roles: list[str] | None = None,
        active: bool | None = None,
    ) -> dict[str, Any]:
        if name is not None and (not name.strip() or len(name.strip()) > 80):
            raise ValueError("Informe o nome (até 80 caracteres).")
        if roles is not None:
            roles = list(dict.fromkeys(r.strip() for r in roles if r.strip()))
            if not roles or len(roles) > 10 or any(len(r) > 60 for r in roles):
                raise ValueError("Escolha funções válidas.")
        async with self.locked(True) as d:
            user = next((u for u in d["users"] if u["id"] == user_id), None)
            if user is None:
                raise LookupError("Usuário não encontrado.")
            admin_after = "Administrador" in (roles or user["roles"]) and (
                user["active"] if active is None else active
            )
            other_admin = any(
                u["active"] and "Administrador" in u["roles"] for u in d["users"] if u["id"] != user_id
            )
            if user["active"] and "Administrador" in user["roles"] and not admin_after and not other_admin:
                raise ValueError("É preciso manter ao menos um administrador ativo.")
            if name is not None:
                user["name"] = name.strip()
            if roles is not None:
                user["roles"] = roles
            if active is not None:
                user["active"] = active
            user["updated_at"] = now_ms()
            result = user.copy()
            if not user["active"]:
                d["sessions"] = [s for s in d["sessions"] if s["user_id"] != user_id]
        if not result["active"] and self.db:
            await self.db.session_delete_user(user_id)
        return cast(dict[str, Any], result)

    async def reset_password(self, user_id: str, password: str) -> dict[str, Any]:
        if err := policy(password):
            raise ValueError(err)
        async with self.locked(True) as d:
            user = next((u for u in d["users"] if u["id"] == user_id), None)
            if user is None:
                raise LookupError("Usuário não encontrado.")
            user.update(
                password_hash=PH.hash(password),
                must_change_password=True,
                failed_attempts=0,
                locked_until=None,
                updated_at=now_ms(),
            )
            d["sessions"] = [s for s in d["sessions"] if s["user_id"] != user_id]
            result = user.copy()
        if self.db:
            await self.db.session_delete_user(user_id)
        return cast(dict[str, Any], result)

    async def unlock(self, user_id: str) -> dict[str, Any]:
        async with self.locked(True) as d:
            user = next((u for u in d["users"] if u["id"] == user_id), None)
            if user is None:
                raise LookupError("Usuário não encontrado.")
            user.update(failed_attempts=0, locked_until=None, updated_at=now_ms())
            return user.copy()

    async def delete_user(self, user_id: str) -> dict[str, Any]:
        async with self.locked(True) as d:
            user = next((u for u in d["users"] if u["id"] == user_id), None)
            if user is None:
                raise LookupError("Usuário não encontrado.")
            if user["active"]:
                raise ValueError("Desative o usuário antes de excluí-lo.")
            d["users"].remove(user)
            d["sessions"] = [s for s in d["sessions"] if s["user_id"] != user_id]
            d["tokens"] = [t for t in d["tokens"] if t.get("owner") != user_id]
        if self.db:
            await self.db.session_delete_user(user_id)
        return user

    async def change_password(self, user: dict[str, Any], current: str, new: str) -> bool:
        if not verify(current, user.get("password_hash")):
            return False
        if current == new:
            raise ValueError("A nova senha deve ser diferente da atual.")
        if err := policy(new):
            raise ValueError(err)
        async with self.locked(True) as d:
            u = next((x for x in d["users"] if x["id"] == user["id"]), None)
            if not u:
                return False
            u.update(password_hash=PH.hash(new), must_change_password=False, updated_at=now_ms())
            d["sessions"] = [s for s in d["sessions"] if s["user_id"] != user["id"]]
        if self.db:
            await self.db.session_delete_user(user["id"])
        return True

    async def create_token(
        self,
        user: dict[str, Any],
        name: str,
        apps: list[str],
        actions: list[str],
        days: int | None = None,
        envs: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        name = name.strip()
        actions = sorted(set(actions))
        if (
            not name
            or len(name) > 60
            or not actions
            or any(a not in TOKEN_ACTIONS for a in actions)
            or (any(a in OWNED_ACTIONS for a in actions) and not user)
        ):
            raise ValueError("Dados de token inválidos.")
        if not 1 <= (days or 90) <= 365:
            raise ValueError("Validade entre 1 e 365 dias.")
        async with self.locked(True) as d:
            owned = any(a in OWNED_ACTIONS for a in actions)
            if (
                owned
                and sum(
                    t.get("owner") == user["id"]
                    and t.get("revoked_at") is None
                    and (t.get("expires_at") is None or t["expires_at"] > now_ms())
                    for t in d["tokens"]
                )
                >= 20
            ):
                raise ValueError("Limite de 20 tokens ativos por usuário.")
            token_id = self._id(d, "t")
            secret = f"hubt_{token_id}_{secrets.token_urlsafe(32)}"
            t = {
                "id": token_id,
                "name": name,
                "hash": sha(secret),
                "prefix": secret[:12],
                "apps": apps,
                "envs": envs or [],
                "actions": actions,
                "created_by": user["name"],
                "created_at": now_ms(),
                "expires_at": now_ms() + (days or 90) * 86_400_000,
                "revoked_at": None,
                "last_used": None,
                "owner": user["id"] if owned else None,
            }
            d["tokens"].append(t)
        return secret, t

    async def bearer(self, value: str) -> dict[str, Any] | None:
        if not value.startswith("hubt_"):
            return None
        async with self.locked() as d:
            t = next((x.copy() for x in d["tokens"] if x["id"] == value[5:].split("_", 1)[0]), None)
        if (
            not t
            or not secrets.compare_digest(t["hash"], sha(value))
            or t.get("revoked_at") is not None
            or t.get("expires_at", 0) <= now_ms()
        ):
            return None
        return cast(dict[str, Any], t)


def user_view(u: dict[str, Any], now: int, locked: bool = False) -> dict[str, Any]:
    """Usuário como a API o mostra (campos e ordem do `UserView` da versão anterior do hub; nunca o hash da senha)."""
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


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    # HUB_EDGE_CIDR = rede da borda (app-hub-edge, do próprio compose): sempre confiável, como os proxies declarados
    trusted = _cidrs(os.getenv("HUB_TRUSTED_PROXIES", "") + "," + os.getenv("HUB_EDGE_CIDR", ""))
    try:
        source = ipaddress.ip_address(peer)
    except ValueError:
        return "?"
    if not any(source in c for c in trusted):
        return str(source)
    entries = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
    for entry in reversed(entries):
        try:
            ip = ipaddress.ip_address(entry)
        except ValueError:
            return "?"
        if not any(ip in c for c in trusted):
            return str(ip)
    return "?"


def _cidrs(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    try:
        return [ipaddress.ip_network(x.strip(), strict=False) for x in raw.split(",") if x.strip()]
    except ValueError:
        return []


def control_allowed(request: Request, mcp_public: bool = False, mcp_path: str = "/mcp") -> bool:
    path = request.url.path
    if mcp_public and (path == mcp_path or path.startswith(("/.well-known/", "/oauth/"))):
        return True
    segs = path.strip("/").split("/")
    # /oauth/authorize é tela de pessoa (como /account); o resto de /oauth segue o plano de controle.
    if segs[0] not in ("api", "admin", "mcp", ".well-known", "oauth") or path == "/oauth/authorize":
        return True
    raw = os.getenv("HUB_ADMIN_ALLOWED_CIDRS")
    if raw is None:
        return True
    try:
        ip = ipaddress.ip_address(client_ip(request))
        return any(ip in c for c in _cidrs(raw))
    except ValueError:
        return False


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
    """Permissões que abrem `/admin/<segment>` (qualquer uma serve)."""
    return _ADMIN_REQUIRED.get(segment, ("editar_configuracoes",))


def api_required(method: str, segs: list[str]) -> tuple[str, ...]:
    """Permissões que abrem `/api/<segs>` (qualquer uma serve); `segs` vem depois de `api`."""
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
