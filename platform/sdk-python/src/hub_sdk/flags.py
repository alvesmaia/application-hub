"""Feature flags (wave 8) — same algorithm as the ``hub-flags`` crate (shared vectors
in ``tests/vectors/flags-vectors.json``).

- **In the request**: :func:`from_headers` reads ``x-hub-flags`` (evaluated by the hub for the
  session's user; the gateway discards the browser's) → :meth:`Flags.is_on` /
  :meth:`Flags.variant`. Flag ausente = desligada.
- **Outside a request** (jobs): :class:`FlagStore` keeps the definitions that the hub
  pushes to ``POST /{app}/_hub/flags`` and evaluates them locally (:meth:`FlagStore.evaluate`).
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any

HEADER = "x-hub-flags"
ON = "on"
OFF = "off"
_TOKEN = re.compile(r"^[a-z0-9._-]{1,64}$")


def valid_token(s: str) -> bool:
    return bool(_TOKEN.match(s))


USER_DIGEST_PREFIX = "sha256:"


def user_digest(key: str, user: str) -> str:
    """User summary in the rules pushed by the hub (the app does not receive emails)."""
    h = hashlib.sha256(f"hub-flag-user:v1:{key}:{user.lower()}".encode()).hexdigest()
    return USER_DIGEST_PREFIX + h


def bucket(key: str, user: str) -> int:
    """Stable bucket 0–9999 of ``key:user`` (sha256, first 4 bytes big-endian)."""
    d = hashlib.sha256(f"{key}:{user}".encode()).digest()
    return int.from_bytes(d[:4], "big") % 10_000


def parse_header(v: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for kv in v.split(","):
        k, sep, x = kv.strip().partition("=")
        if sep and valid_token(k) and valid_token(x):
            out[k] = x
    return out


class Flags:
    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self.values: dict[str, str] = dict(values) if values is not None else {}

    def is_on(self, key: str) -> bool:
        v = self.values.get(key)
        return v is not None and v != OFF

    def variant(self, key: str) -> str | None:
        return self.values.get(key)


def from_headers(headers: Mapping[str, str]) -> Flags:
    return Flags(parse_header(headers.get(HEADER, "") or ""))


def _off_value(flag: Mapping[str, Any]) -> str:
    if flag.get("kind") == "variant":
        vs = flag.get("variants") or []
        return str(vs[0]) if vs else OFF
    return OFF


def evaluate(flag: Mapping[str, Any], env: str, user: str, roles: Sequence[str]) -> str:
    """Value of ``flag`` in the environment for the user (order: kill switch →
    rules → percentage → default)."""
    r = (flag.get("envs") or {}).get(env)
    if not r or not r.get("enabled"):
        return _off_value(flag)
    digest = user_digest(str(flag["key"]), user) if user else ""
    for rule in r.get("rules") or []:
        users = [u if u.startswith(USER_DIGEST_PREFIX) else u.lower() for u in rule.get("users") or []]
        by_user = bool(user) and (user.lower() in users or digest in users)
        if by_user or any(x in roles for x in rule.get("roles") or []):
            return str(rule["serve"])
    ro = r.get("rollout")
    if ro and user and bucket(str(flag["key"]), user) < int(ro["percent"]) * 100:
        return str(ro["serve"])
    return str(r["default"])


class FlagStore:
    """Definitions pushed by the hub (local cache; empty until the first push)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.version = 0
        self.env = ""
        self.flags: list[dict[str, Any]] = []

    def replace(self, version: int, env: str, flags: list[dict[str, Any]]) -> bool:
        with self._lock:
            if version < self.version:
                return False
            self.version, self.env, self.flags = version, env, flags
            return True

    def evaluate(self, key: str, user: str, roles: Sequence[str]) -> str:
        with self._lock:
            f = next((x for x in self.flags if x.get("key") == key), None)
            env = self.env
        return evaluate(f, env, user, roles) if f is not None and f else OFF

    def is_on(self, key: str, user: str, roles: Sequence[str]) -> bool:
        return self.evaluate(key, user, roles) != OFF


def validate_push(body: Any) -> tuple[int, str, list[dict[str, Any]]] | None:
    """Validates the body of ``POST /_hub/flags`` (``None`` = invalid)."""
    if not isinstance(body, dict):
        return None
    v, env, flags = body.get("version"), body.get("env"), body.get("flags")
    if not isinstance(v, int) or env not in ("staging", "producao") or not isinstance(flags, list):
        return None
    if len(flags) > 500:
        return None
    for f in flags:
        if not isinstance(f, dict) or not valid_token(str(f.get("key", ""))):
            return None
    return v, env, flags
