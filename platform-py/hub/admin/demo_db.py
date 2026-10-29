"""Own role for each sample app in the apps' Postgres: owner of its database, no superuser, the per-app limits, no access to
the neighbours. Run on every `up` by the `app-hub-db-roles` job (`python -m hub.admin demo-db-roles`): idempotent, and it
also converts an existing install whose sample apps still used the superuser `hub` (the tables made by `hub` change owner)."""

# ruff: noqa: S608 - admin SQL with escaped literals/identifiers (`_lit`/`_qi`)
from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping

import asyncpg

from hub.ops.backup_db import _lit, _qi, chown_sql
from hub.ops.provision import limits_sql

DEMO_APPS = ("ativos", "fornecedores", "servicos", "coletor")
HOST = "app-hub-app-db:5432"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def password_env(app: str) -> str:
    return f"APP_HUB_APP_DB_PASSWORD_{app.upper()}"


def marker(app: str) -> str:
    return f"hub-managed:demo={app}"


def role_sql(app: str, pw: str) -> list[str]:
    """Statements on the `postgres` database; a role of the same name not made here aborts (`HUB-ALIEN-ROLE`)."""
    if not _NAME.match(app):
        raise ValueError(f"nome de app inválido para papel: {app!r} (esperado [a-z][a-z0-9_]*)")
    r, m, q = _lit(app), _lit(marker(app)), _qi(app)
    create = (
        "DO $$DECLARE c text; BEGIN "
        f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {r}) THEN "
        f"SELECT coalesce(shobj_description(oid, 'pg_authid'), '') INTO c FROM pg_roles WHERE rolname = {r}; "
        f"IF c <> {m} THEN RAISE EXCEPTION 'HUB-ALIEN-ROLE'; END IF; "
        f"EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', {r}, {_lit(pw)}); "
        f"ELSE EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 40 "
        f"PASSWORD %L', {r}, {_lit(pw)}); EXECUTE format('COMMENT ON ROLE %I IS %L', {r}, {m}); END IF; END$$"
    )
    return [
        create,
        f"ALTER DATABASE {q} OWNER TO {q}",
        f"REVOKE ALL ON DATABASE {q} FROM PUBLIC",
        f"GRANT CONNECT, TEMPORARY ON DATABASE {q} TO {q}",
        *limits_sql(app),
    ]


def superuser_dsn_pairs(superuser_pw: str, pws: dict[str, str]) -> dict[str, str]:
    """Variables seeded by an older install (`hub:<superuser password>`) → the app's own role."""
    return {
        f"postgresql://hub:{superuser_pw}@{HOST}/{a}": f"postgresql://{a}:{pw}@{HOST}/{a}"
        for a, pw in pws.items()
    }


def migration_pairs(env: Mapping[str, str]) -> dict[str, str]:
    """Boot of the hub: only with the configured passwords (`.prod.env`); the dev stack keeps its seeded values."""
    su = env.get("APP_HUB_APP_DB_PASSWORD", "").strip()
    pws = {a: env.get(password_env(a), "").strip() for a in DEMO_APPS}
    return superuser_dsn_pairs(su, {a: p for a, p in pws.items() if len(p) >= 16}) if len(su) >= 16 else {}


async def _connect(url: str) -> asyncpg.Connection:
    for _ in range(60):  # the job starts together with the database
        try:
            return await asyncpg.connect(url)
        except (OSError, asyncpg.CannotConnectNowError):
            await asyncio.sleep(1)
    return await asyncpg.connect(url)


async def apply(admin_url: str, pws: dict[str, str]) -> list[str]:
    """`admin_url` = the superuser on the `postgres` database; returns the apps whose role is ready."""
    su = await _connect(admin_url)
    try:
        for app, pw in pws.items():
            if not await su.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", app):
                await su.execute(f"CREATE DATABASE {_qi(app)}")
            for stmt in role_sql(app, pw):
                await su.execute(stmt)
            db = await asyncpg.connect(admin_url.rsplit("/", 1)[0] + f"/{app}")
            try:
                await db.execute(chown_sql(app))
            finally:
                await db.close()
    finally:
        await su.close()
    return list(pws)


def main() -> int:
    url = os.environ["HUB_APPDB_ADMIN_URL"]
    pws = {a: os.getenv(password_env(a), "") for a in DEMO_APPS}
    if missing := [password_env(a) for a, pw in pws.items() if not pw]:
        raise SystemExit(f"demo-db-roles: defina {', '.join(missing)}")
    print("papéis dos apps de exemplo prontos:", ", ".join(asyncio.run(apply(url, pws))))
    return 0
