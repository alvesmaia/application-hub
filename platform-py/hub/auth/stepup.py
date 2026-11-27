"""Step-up: the person in a browser session confirms a destructive action with their own password.

At most 5 wrong passwords per person every 15 minutes (shared by every action that asks for it)."""

from __future__ import annotations

from typing import Any, cast

from fastapi import Request

from hub.auth import now_ms, verify

TOO_MANY = "Muitas tentativas. Aguarde alguns minutos e tente de novo."
WINDOW_MS, MAX_TRIES = 15 * 60_000, 5


def _tries(request: Request, user_id: str) -> list[int]:
    st = request.app.state
    if getattr(st, "stepup_fails", None) is None:
        st.stepup_fails = {}
    fails = cast(dict[str, list[int]], st.stepup_fails)
    tries = fails.setdefault(user_id, [])
    cut = now_ms() - WINDOW_MS
    tries[:] = [x for x in tries if x > cut]
    return tries


async def _hash_of(request: Request, user_id: str) -> Any:
    async with request.app.state.auth.locked() as d:
        row = next((x for x in d["users"] if x["id"] == user_id), None)
    return row.get("password_hash") if row else None


async def check_password(request: Request, password: str, wrong: str) -> str | None:
    """None when the session's user typed their password; else the message (`wrong`, or too many tries).
    Callers decide what a token (no session) may do."""
    uid = str(request.state.user.get("id") or "")
    tries = _tries(request, uid)
    if len(tries) >= MAX_TRIES:
        return TOO_MANY
    if uid and password and verify(password, await _hash_of(request, uid)):
        return None
    tries.append(now_ms())
    return wrong
