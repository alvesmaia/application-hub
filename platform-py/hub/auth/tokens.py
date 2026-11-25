"""Personal tokens of `AuthStore` (every token has an owner): create, check, revoke all of a person."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, cast

from hub.auth.passwords import sha
from hub.auth.rbac import TOKEN_ACTIONS
from hub.state import StateDb


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class TokenOps:
    """Mixed into `AuthStore`: needs its `locked()` and `db`."""

    db: StateDb | None
    locked: Callable[..., AbstractAsyncContextManager[dict[str, Any]]]
    _id: Callable[[dict[str, Any], str], str]

    async def revoke_access(self, user_id: str) -> int:
        """Ends every session and revokes every token of the person; returns how many tokens were live."""
        n = now_ms()
        async with self.locked(True) as d:
            live = [t for t in d["tokens"] if t.get("owner") == user_id and t.get("revoked_at") is None]
            for t in live:
                t["revoked_at"] = n
            d["sessions"] = [s for s in d["sessions"] if s["user_id"] != user_id]
        if self.db:
            await self.db.session_delete_user(user_id)
        return len(live)

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
            or not user
        ):
            raise ValueError("Dados de token inválidos.")
        if not 1 <= (days or 90) <= 365:
            raise ValueError("Validade entre 1 e 365 dias.")
        async with self.locked(True) as d:
            if (
                sum(
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
                "owner": user["id"],
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


def adopt_owner_less_tokens(d: dict[str, Any]) -> None:
    """Tokens from the old platform page had no owner: each goes to the one person whose name created it; with no
    such person (or two people with that name) it is revoked rather than guessed."""
    by_name: dict[str, list[str]] = {}
    for u in d["users"]:
        by_name.setdefault(u["name"], []).append(u["id"])
    for t in d["tokens"]:
        if t.get("owner") or t.get("revoked_at") is not None:
            continue
        ids = by_name.get(t.get("created_by", ""), [])
        if len(ids) == 1:
            t["owner"] = ids[0]
        else:
            t["revoked_at"] = now_ms()
