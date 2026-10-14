"""Connection settings and the value types the state database returns."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,47}$")
DB_NOW = "(extract(epoch from clock_timestamp()) * 1000)::bigint"


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def default_replica_id() -> str:
    base = os.getenv("HUB_REPLICA_ID") or os.getenv("HOSTNAME") or socket.gethostname() or "hub"
    base = "".join(c for c in base if c.isascii() and (c.isalnum() or c in "-_."))[:48]
    return f"{base}-{secrets.token_hex(3)}"


@dataclass
class Config:
    url: str
    schema: str = "hub"
    pool: int = 8
    replica: str = ""

    def __post_init__(self) -> None:
        if not _IDENT.fullmatch(self.schema):
            raise ValueError(f"invalid schema: {self.schema}")
        if self.pool < 1:
            raise ValueError("pool must be positive")
        if not self.replica:
            self.replica = default_replica_id()

    @classmethod
    def from_env(cls) -> Config | None:
        url = os.getenv("HUB_STATE_DB_URL", "").strip()
        if not url:
            return None
        schema = os.getenv("HUB_STATE_DB_SCHEMA", "hub")
        if not _IDENT.fullmatch(schema):
            schema = "hub"
        try:
            pool = max(1, int(os.getenv("HUB_STATE_DB_POOL", "12")))
        except ValueError:
            pool = 12
        return cls(url, schema, pool)


@dataclass(frozen=True)
class Change:
    action: str
    value: Any = None
    expires_at: int = 0

    @classmethod
    def keep(cls) -> Change:
        return cls("keep")

    @classmethod
    def set(cls, value: Any, expires_at: int) -> Change:
        return cls("set", value, expires_at)

    @classmethod
    def delete(cls) -> Change:
        return cls("delete")


@dataclass(frozen=True)
class SessionRow:
    id_hash: str
    user_id: str
    created_at: int
    last_seen: int
    method: str = ""


@dataclass(frozen=True)
class SessionLimits:
    idle_ms: int
    absolute_ms: int
    max_per_user: int


@dataclass(frozen=True)
class LeaseInfo:
    holder: str
    fence: int
    expires_in_ms: int
    acquired_at: int
