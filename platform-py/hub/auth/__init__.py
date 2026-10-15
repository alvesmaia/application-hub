"""Local users, sessions, API tokens and route permissions (format compatible with the previous hub version)."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from hub.auth.authfile import (
    bootstrap_password,
    demo_shared_password,
    demo_users_enabled,
    write_auth_file,
    write_bootstrap_note,
)
from hub.auth.net import _cidrs as _cidrs
from hub.auth.net import client_ip as client_ip
from hub.auth.net import control_allowed as control_allowed
from hub.auth.passwords import DUMMY_HASH as DUMMY_HASH
from hub.auth.passwords import PH as PH
from hub.auth.passwords import normalize_email as normalize_email
from hub.auth.passwords import policy as policy
from hub.auth.passwords import sha as sha
from hub.auth.passwords import verify as verify
from hub.auth.rbac import OWNED_ACTIONS as OWNED_ACTIONS
from hub.auth.rbac import PERMISSIONS as PERMISSIONS
from hub.auth.rbac import TOKEN_ACTIONS as TOKEN_ACTIONS
from hub.auth.rbac import admin_required as admin_required
from hub.auth.rbac import api_required as api_required
from hub.auth.rbac import owned_surface as owned_surface
from hub.auth.rbac import permissions as permissions
from hub.auth.rbac import roles_for as roles_for
from hub.auth.rbac import token_action as token_action
from hub.auth.rbac import token_covers as token_covers
from hub.auth.rbac import user_view as user_view
from hub.models import AuthDocument
from hub.state import SessionLimits, SessionRow, StateDb

COOKIE = "hub_session"
SSO_COOKIE = "hub_sso"


def positive_env(name: str, default: int, cap: int) -> int:
    try:
        return min(cap, max(1, int(os.getenv(name, str(default)).strip())))
    except ValueError:
        return default


IDLE_MS = positive_env("HUB_SESSION_IDLE_MIN", 30, 24 * 60) * 60_000
ABSOLUTE_MS = positive_env("HUB_SESSION_ABSOLUTE_HOURS", 12, 24 * 365) * 3_600_000
MAX_SESSIONS = positive_env("HUB_MAX_SESSIONS_PER_USER", 10, 1000)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _user_login(users: list[dict[str, Any]], login: str) -> dict[str, Any] | None:
    key = login.strip().lower()
    found = [u for u in users if u["email"] == key]
    if not found and "@" not in key:
        found = [u for u in users if u["email"].split("@", 1)[0] == key]
    demos = [u for u in found if u["email"].endswith("@demo.local")]
    return demos[0] if len(demos) == 1 else found[0] if len(found) == 1 else None


_DEMO_USERS = (  # (email, name, role, password when HUB_DEMO_PASSWORD is not set)
    ("admin@demo.local", "Administrador", "Administrador", "admin"),
    ("operador@demo.local", "Operador", "Tech Lead", "operador"),
    ("leitor@demo.local", "Leitor", "Colaborador", "leitor"),
)


def _clean_roles(roles: list[str]) -> list[str]:
    roles = list(dict.fromkeys(r.strip() for r in roles if r.strip()))
    if not roles or len(roles) > 10 or any(len(r) > 60 for r in roles):
        raise ValueError("Escolha funções válidas.")
    return roles


def _keep_one_admin(
    users: list[dict[str, Any]], user: dict[str, Any], roles: list[str] | None, active: bool | None
) -> None:
    admin_after = "Administrador" in (roles or user["roles"]) and (
        user["active"] if active is None else active
    )
    other_admin = any(u["active"] and "Administrador" in u["roles"] for u in users if u["id"] != user["id"])
    if user["active"] and "Administrador" in user["roles"] and not admin_after and not other_admin:
        raise ValueError("É preciso manter ao menos um administrador ativo.")


def _apply_user_changes(
    user: dict[str, Any], name: str | None, roles: list[str] | None, active: bool | None
) -> None:
    if name is not None:
        user["name"] = name.strip()
    if roles is not None:
        user["roles"] = roles
    if active is not None:
        user["active"] = active
    user["updated_at"] = now_ms()


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
        async with self._lock:  # noqa: PLR1702
            if self.db:  # the write stays inside the cross-replica lock, or another replica can overwrite it
                async with self.db.file_lock("auth.json", write=write):
                    data = self._read()
                    yield data
                    if write:
                        write_auth_file(self.path, data, True)
            else:
                data = self._read()
                yield data
                if write:
                    write_auth_file(self.path, data, False)

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
            if demo_users_enabled():
                self._bootstrap_demo(d)
                return
            if d["users"]:
                return
            email = os.getenv("HUB_BOOTSTRAP_ADMIN_EMAIL", "admin@hub.local").strip().lower()
            password, generated = bootstrap_password()
            u = self._new_user(d, email, "Administrador", "Administrador", password, generated)
            d["users"].append(u)
            if generated:
                write_bootstrap_note(self.path.parent, email, password)

    def _bootstrap_demo(self, d: dict[str, Any]) -> None:
        shared = demo_shared_password()
        for email, name, role, default in _DEMO_USERS:
            password = shared or default
            u = next((x for x in d["users"] if x["email"] == email), None)
            if u is None:
                u = self._new_user(d, email, name, role, password, False)
                d["users"].append(u)
            else:
                u.update(name=name, password_hash=PH.hash(password), must_change_password=False)

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
        """SSO user: by link (issuer+subject), otherwise by email (links it), otherwise creates it (JIT).
        `roles` (from the group mapping) replaces the roles when not empty. Disabled: (None, False)."""
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
            if user.get("must_change_password"):
                # Temporary password never changed: voided on linking (SSO does not require the change, and it stays inactive).
                user.update(password_hash=None, must_change_password=False)
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

    async def logout(self, token: str) -> str:
        """Ends the session and returns how it was opened (`password`, `entra`…; empty if it did not exist)."""
        h = sha(token)
        if self.db:
            s = await self.db.session_get(h)
            await self.db.session_delete(h)
            return s.method if s else ""
        async with self.locked(True) as d:
            old = next((s for s in d["sessions"] if s["id_hash"] == h), None)
            d["sessions"] = [s for s in d["sessions"] if s["id_hash"] != h]
        return str(old.get("method", "")) if old else ""

    async def create_user(
        self, email: str, name: str, roles: list[str], password: str, must_change: bool = True
    ) -> dict[str, Any]:
        email = normalize_email(email)
        name = name.strip()
        if not name or len(name) > 80 or any(unicodedata.category(c) == "Cc" for c in name):
            raise ValueError("Informe o nome (até 80 caracteres).")
        roles = _clean_roles(roles)
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
            roles = _clean_roles(roles)
        async with self.locked(True) as d:
            user = next((u for u in d["users"] if u["id"] == user_id), None)
            if user is None:
                raise LookupError("Usuário não encontrado.")
            _keep_one_admin(d["users"], user, roles, active)
            _apply_user_changes(user, name, roles, active)
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
