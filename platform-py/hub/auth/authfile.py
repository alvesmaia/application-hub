"""Persistence and first-run helpers of the `auth.json` document (kept out of `AuthStore` to keep it small)."""

from __future__ import annotations

import json
import os
import secrets
from contextlib import suppress
from pathlib import Path
from typing import Any

from hub.auth.passwords import policy

_BOOTSTRAP_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789-_.!@#%+="


def write_auth_file(path: Path, data: dict[str, Any], db_backed: bool) -> None:
    """Atomic write; with the state DB, sessions live in Postgres and are never persisted to the file."""
    data.pop("dropped", None)
    if db_backed:
        data["sessions"] = []
    for user in data["users"]:
        for key in ("password_hash", "sso_subject", "sso_issuer"):
            if user.get(key) is None:
                user.pop(key, None)
    for token in data["tokens"]:
        if token.get("owner") is None:
            token.pop("owner", None)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    with suppress(OSError):
        tmp.chmod(0o600)
    os.replace(tmp, path)


def demo_users_enabled() -> bool:
    return os.getenv("HUB_DEMO_USERS", "").lower() in ("1", "true", "yes", "on")


def demo_shared_password() -> str | None:
    """`HUB_DEMO_PASSWORD` when it meets the policy; otherwise each demo user keeps its default."""
    shared = os.getenv("HUB_DEMO_PASSWORD") or None
    if shared and policy(shared):
        return None
    return shared


def bootstrap_password() -> tuple[str, bool]:
    """(password, generated): the configured one (must meet the policy) or a random one that does."""
    configured = os.getenv("HUB_BOOTSTRAP_ADMIN_PASSWORD")
    password = configured or ""
    while not configured and policy(password):
        password = "".join(secrets.choice(_BOOTSTRAP_ALPHABET) for _ in range(20))
    if err := policy(password):
        raise ValueError(f"HUB_BOOTSTRAP_ADMIN_PASSWORD: {err}")
    return password, not configured


def write_bootstrap_note(folder: Path, email: str, password: str) -> None:
    f = folder / "bootstrap-admin.txt"
    f.write_text(
        f"Administrador inicial da plataforma\nE-mail: {email}\nSenha: {password}\n\nTroque a senha no primeiro acesso.\n",
        encoding="utf-8",
    )
    f.chmod(0o600)
