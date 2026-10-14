"""Identity injected by the hub in the ``x-hub-user`` header.

Format (contract, see ``docs/CONTRATO-APP.md``): JSON in **base64url without padding**::

    {"user": "ana@corp.com", "roles": ["Administrador"], "id": "u1a2b3c4",
     "name": "Ana", "email": "ana@corp.com", "permissions": ["acessar_aplicacoes"]}

``user`` (login) and ``roles`` always present; the others only when filled.

Trust rules: the gateway **always** discards any ``x-hub-*`` coming from the browser and injects its own —
the app trusts the header because only the hub can reach it (internal network
``hub-app-<id>``). Missing or invalid → guest identity (the app must never treat
that as administrator).
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field

from starlette.requests import HTTPConnection

USER_HEADER = "x-hub-user"
ENV_HEADER = "x-hub-env"
GUEST = "convidado"
# Cap on the decoded header (the hub never sends more than a few KiB).
MAX_HEADER = 16 * 1024


@dataclass(frozen=True)
class Identity:
    user: str
    roles: tuple[str, ...] = ()
    id: str = ""
    name: str = ""
    email: str = ""
    permissions: tuple[str, ...] = field(default_factory=tuple)
    # User language (wave 9): ``pt-BR`` or ``en``; empty on older hubs.
    locale: str = ""

    @property
    def display_name(self) -> str:
        """Display name (`name`, otherwise the login)."""
        return self.name.strip() or self.user

    @property
    def is_guest(self) -> bool:
        return self.user == GUEST and not self.roles

    def has_permission(self, p: str) -> bool:
        return p in self.permissions

    def encode(self) -> str:
        """Header value (for tests and for `hub dev`)."""
        d: dict[str, object] = {"user": self.user, "roles": list(self.roles)}
        for k in ("id", "name", "email", "locale"):
            v = getattr(self, k)
            if v:
                d[k] = v
        if self.permissions:
            d["permissions"] = list(self.permissions)
        raw = json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


GUEST_IDENTITY = Identity(user=GUEST)


def _str_list(v: object) -> tuple[str, ...]:
    if not isinstance(v, list):
        return ()
    return tuple(x for x in v if isinstance(x, str))


def parse_identity(value: str | None) -> Identity | None:
    """Decodes the header; ``None`` if missing or invalid."""
    if value is None or not value:
        return None
    v = value.strip()
    if not v or len(v) > MAX_HEADER * 2:
        return None
    try:
        raw = base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))
        data = json.loads(raw)
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    user = data.get("user")
    if not isinstance(user, str) or not user.strip():
        return None

    def s(k: str) -> str:
        x = data.get(k, "")
        return x if isinstance(x, str) else ""

    return Identity(
        user=user.strip(),
        roles=_str_list(data.get("roles")),
        id=s("id"),
        name=s("name"),
        email=s("email"),
        permissions=_str_list(data.get("permissions")),
        locale=s("locale"),
    )


def identity_of(conn: HTTPConnection) -> Identity:
    """Request identity (guest if missing or invalid). Use as a dependency
    of FastAPI: ``ident: Identity = Depends(identity_of)``."""
    return parse_identity(conn.headers.get(USER_HEADER)) or GUEST_IDENTITY


def user_key(headers_value: str | None) -> str:
    """Raw header value, trimmed — it is the scope of the idempotency keys (another user's key never returns this user's response)."""
    return (headers_value or "").strip()


def env_of(conn: HTTPConnection) -> str:
    """Environment declared by the hub (`producao`/`staging`); default is production."""
    v = conn.headers.get(ENV_HEADER, "producao").strip()
    return v if v in ("producao", "staging") else "producao"
