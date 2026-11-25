"""Local users, sessions, API tokens and route permissions (format compatible with the previous hub version)."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
import unicodedata
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast, override

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
from hub.auth.tokens import TokenOps, adopt_owner_less_tokens
from hub.models import AuthDocument
from hub.state import SessionLimits, SessionRow, StateDb

COOKIE = "hub_session"
SSO_COOKIE = "hub_sso"


def positive_env(name: str, default: int, cap: int) -> int:
    try:
        return min(cap, max(1, int(os.getenv(name, str(default)).strip())))
    except ValueError:
        return default


# A session unused for IDLE_MS ends; browsing renews it at most once per RENEW_MS (one write per hour, not per
# request); ABSOLUTE_MS after the login it ends anyway.
IDLE_MS = positive_env("HUB_SESSION_IDLE_MIN", 24 * 60, 24 * 60) * 60_000
RENEW_MS = min(IDLE_MS, positive_env("HUB_SESSION_RENEW_MIN", 60, 24 * 60) * 60_000)
ABSOLUTE_MS = positive_env("HUB_SESSION_ABSOLUTE_HOURS", 72, 24 * 365) * 3_600_000
MAX_SESSIONS = positive_env("HUB_MAX_SESSIONS_PER_USER", 10, 1000)


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _user_login(users: list[dict[str, Any]], login: str) -> dict[str, Any] | None:
    key = login.strip().lower()
    found = [u for u in users if u["email"] == key]
    found = [
        u for u in found if u.get("password_hash")
    ] or found  # the SSO twin of a local admin has no password
    if not found and "@" not in key:
        found = [u for u in users if u["email"].split("@", 1)[0] == key]
    demos = [u for u in found if u["email"].endswith("@demo.local")]
    return demos[0] if len(demos) == 1 else found[0] if len(found) == 1 else None


_DEMO_USERS = (  # (email, name, role, password when HUB_DEMO_PASSWORD is not set)
    ("admin@demo.local", "Administrador", "Administrador", "admin"),
    ("operador@demo.local", "Operador", "Power User", "operador"),
    ("leitor@demo.local", "Leitor", "User", "leitor"),
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
class AuthStore(TokenOps):
    path: Path
    db: StateDb | None = None
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        if not self.path.exists():
            self.path.write_text('{"users":[],"sessions":[],"tokens":[],"next_id":0}', encoding="utf-8")

    @override
    @asynccontextmanager
    async def locked(self, write: bool = False) -> AsyncIterator[dict[str, Any]]:
        # The write stays inside the cross-replica lock, or another replica can overwrite it.
        async with self._lock, self.db.file_lock("auth.json", write=write) if self.db else nullcontext():
            data = self._read()
            yield data
            if write:
                write_auth_file(self.path, data, self.db is not None)

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        data = {"users": [], "sessions": [], "tokens": [], "next_id": 0, **data}
        AuthDocument.model_validate(data)
        return data

    @override
    def _id(self, d: dict[str, Any], prefix: str) -> str:
        d["next_id"] += 1
        return f"{prefix}{d['next_id']}{secrets.token_hex(4)}"

    async def bootstrap(self) -> None:
        async with self.locked(True) as d:
            adopt_owner_less_tokens(d)
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
        self,
        issuer: str,
        subject: str,
        email: str,
        name: str,
        roles: list[str] | None,
        default_role: str,
        local_admin: Callable[[dict[str, Any]], bool] = lambda _u: False,
    ) -> tuple[dict[str, Any] | None, bool]:
        """SSO user: by link (issuer+subject), otherwise by email (links it), otherwise creates it (JIT).
        `roles` (from the group mapping) replaces the roles when not empty. Disabled: (None, False).
        Roles come from SSO: a `local_admin` account (local password + admin permission, e.g. the installer's) is
        never adopted, not even when linked before; the SSO login gets its own account with the default role."""
        async with self.locked(True) as d:
            for u in d[
                "users"
            ]:  # the installer's account takes the person's real name (bootstrap placeholder)
                if u["email"] == email and u["name"] == "Administrador" and name.strip():
                    u.update(name=name.strip()[:80], updated_at=now_ms())
            pool = [u for u in d["users"] if not local_admin(u)]
            user = next(
                (u for u in pool if u.get("sso_issuer") == issuer and u.get("sso_subject") == subject), None
            ) or next((u for u in pool if u["email"] == email), None)
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
            if n - s.last_seen >= RENEW_MS:
                await self.db.session_touch(h, n, RENEW_MS)
            user_id = s.user_id
        else:
            async with self.locked(True) as d:
                s = next((x for x in d["sessions"] if x["id_hash"] == h), None)
                if not s:
                    return None
                if n - s["last_seen"] >= IDLE_MS or n - s["created_at"] >= ABSOLUTE_MS:
                    d["sessions"].remove(s)
                    return None
                if n - s["last_seen"] >= RENEW_MS:
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
