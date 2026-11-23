"""Who is an app admin: a hub role listed in ``AUTOMACOES_ADMIN_ROLES`` (comma-separated; default
``Administrador,Power User``). Admins manage packages, processes and triggers and see every run;
everyone else runs enabled processes and sees only their own runs."""

from __future__ import annotations

import os

from hub_sdk.identity import Identity, identity_of
from starlette.requests import HTTPConnection

DEFAULT_ADMIN_ROLES = "Administrador,Power User"


def admin_roles() -> frozenset[str]:
    raw = os.environ.get("AUTOMACOES_ADMIN_ROLES", "").strip() or DEFAULT_ADMIN_ROLES
    return frozenset(r.strip() for r in raw.split(",") if r.strip())


def is_admin_identity(ident: Identity | None) -> bool:
    return ident is not None and bool(admin_roles() & set(ident.roles))


def is_admin(req: HTTPConnection) -> bool:
    return is_admin_identity(identity_of(req))


def login_of(req: HTTPConnection) -> str:
    return identity_of(req).user


def visible_login(req: HTTPConnection) -> str:
    """Run filter: ``""`` (everyone's) for admins, the user's own login otherwise."""
    return "" if is_admin(req) else login_of(req)
