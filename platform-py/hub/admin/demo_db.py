"""Own role for each sample app in the apps' Postgres: owner of its database, no superuser, the per-app limits, no access to
the neighbours. Database and role are `app_<id>`, like any app the hub provisions. Run on every `up` by the
`app-hub-db-roles` job (`python -m hub.admin demo-db-roles`): idempotent, and it also converts an existing install whose
sample apps still used the superuser `hub` (the tables made by `hub` change owner) or the bare names `<id>` (before 0.8:
the database, the role and the read group are renamed, so the data stays)."""

# ruff: noqa: S608 - admin SQL with escaped literals/identifiers (`_lit`/`_qi`)
from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping

import asyncpg

from hub.admin.config import DEV_INFRA_PASSWORDS
from hub.admin.dbaccess_sql import GROUP_PREFIX
from hub.admin.dbaccess_sql import ROLE_PREFIX as READ_PREFIX
from hub.ops.backup_db import _lit, _qi, chown_sql
from hub.ops.provision import DB_PREFIX, limits_sql

DEMO_APPS = ("inventory", "suppliers", "servicedesk", "collector", "automations")
HOST = "app-hub-app-db:5432"
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def db_of(app: str) -> str:
    """Database and role of a sample app: the same `app_<id>` as any app (hub/ops/provision.py)."""
    return DB_PREFIX + app


def password_env(app: str) -> str:
    return f"APP_HUB_APP_DB_PASSWORD_{app.upper()}"


def marker(app: str) -> str:
    return f"hub-managed:demo={app}"


def role_sql(app: str, pw: str) -> list[str]:
    """Statements on the `postgres` database; a role of the same name not made here aborts (`HUB-ALIEN-ROLE`)."""
    if not _NAME.match(app):
        raise ValueError(f"nome de app inválido para papel: {app!r} (esperado [a-z][a-z0-9_]*)")
    name = db_of(app)
    r, m, q = _lit(name), _lit(marker(app)), _qi(name)
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
        *limits_sql(name),
    ]


def _dsn(app: str, pw: str) -> str:
    return f"postgresql://{db_of(app)}:{pw}@{HOST}/{db_of(app)}"


def superuser_dsn_pairs(superuser_pw: str, pws: dict[str, str]) -> dict[str, str]:
    """Variables seeded by an older install (`hub:<superuser password>`) → the app's own role."""
    return {f"postgresql://hub:{superuser_pw}@{HOST}/{a}": _dsn(a, pw) for a, pw in pws.items()}


def bare_dsn_pairs(pws: dict[str, str]) -> dict[str, str]:
    """Variables seeded before 0.8 (role and database `<id>`) → `app_<id>`; the new value never contains the old one."""
    return {f"postgresql://{a}:{pw}@{HOST}/{a}": _dsn(a, pw) for a, pw in pws.items() if pw}


def migration_pairs(env: Mapping[str, str]) -> dict[str, str]:
    """Boot of the hub: the stored connections follow the job's renames. The superuser move needs the configured
    passwords (`.prod.env`); the move to `app_<id>` also covers the dev stack's seeded passwords."""
    su = env.get("APP_HUB_APP_DB_PASSWORD", "").strip()
    pws = {a: env.get(password_env(a), "").strip() or DEV_INFRA_PASSWORDS[password_env(a)] for a in DEMO_APPS}
    strong = {a: p for a, p in pws.items() if len(p) >= 16}
    return {**(superuser_dsn_pairs(su, strong) if len(su) >= 16 else {}), **bare_dsn_pairs(pws)}


def _read_accounts_sql(old: str, new: str) -> str:
    """Read accounts (hub/admin/dbaccess_sql.py) keep in their comment the databases they read: `old` → `new`."""
    o, n = _lit(old), _lit(new)
    return (
        "DO $$DECLARE r record; m jsonb; BEGIN FOR r IN SELECT rolname, shobj_description(oid, 'pg_authid') c "
        f"FROM pg_roles WHERE starts_with(rolname, {_lit(READ_PREFIX)}) LOOP "
        "BEGIN m := r.c::jsonb; EXCEPTION WHEN others THEN CONTINUE; END; "
        f"IF jsonb_typeof(m->'dbs') = 'array' AND m->'dbs' ? {o} THEN "
        f"m := jsonb_set(m, '{{dbs}}', (SELECT jsonb_agg(CASE WHEN x = {o} THEN {n} ELSE x END) "
        "FROM jsonb_array_elements_text(m->'dbs') x)); "
        "EXECUTE format('COMMENT ON ROLE %I IS %L', r.rolname, m::text); END IF; END LOOP; END$$"
    )


async def _rename(su: asyncpg.Connection, old: str, new: str, catalog: str) -> bool:
    """`catalog` = DATABASE or ROLE: renames `old` only while `new` does not exist (a second run does nothing)."""
    table, col = ("pg_database", "datname") if catalog == "DATABASE" else ("pg_roles", "rolname")
    exists = f"SELECT 1 FROM {table} WHERE {col} = $1"
    if not await su.fetchval(exists, old) or await su.fetchval(exists, new):
        return False
    if catalog == "DATABASE":  # nobody may be connected to a database being renamed (the apps reconnect)
        await su.execute(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = {_lit(old)}"
        )
    await su.execute(f"ALTER {catalog} {_qi(old)} RENAME TO {_qi(new)}")
    return True


async def _move_bare(su: asyncpg.Connection, app: str) -> None:
    """Before 0.8 the sample apps used `<id>` for the database and the role: same data, new names."""
    new = db_of(app)
    if await _rename(su, app, new, "DATABASE"):
        await su.execute(_read_accounts_sql(app, new))
    mark = await su.fetchval(
        "SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = $1", app
    )
    if mark == marker(app):  # only the role this job made, never someone else's role of the same name
        await _rename(su, app, new, "ROLE")
    await _rename(su, GROUP_PREFIX + app, GROUP_PREFIX + new, "ROLE")


async def _connect(url: str) -> asyncpg.Connection:
    for _ in range(60):  # the job starts together with the database
        try:
            return await asyncpg.connect(url)
        except (OSError, asyncpg.CannotConnectNowError):
            await asyncio.sleep(1)
    return await asyncpg.connect(url)


async def _ensure(su: asyncpg.Connection, admin_url: str, app: str, pw: str) -> None:
    name = db_of(app)
    await _move_bare(su, app)
    if not await su.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
        await su.execute(f"CREATE DATABASE {_qi(name)}")
    for stmt in role_sql(app, pw):
        await su.execute(stmt)
    db = await asyncpg.connect(admin_url.rsplit("/", 1)[0] + f"/{name}")
    try:
        await db.execute(chown_sql(name))
    finally:
        await db.close()


async def apply(admin_url: str, pws: dict[str, str]) -> list[str]:
    """`admin_url` = the superuser on the `postgres` database; returns the apps whose role is ready."""
    su = await _connect(admin_url)
    try:
        for app, pw in pws.items():
            await _ensure(su, admin_url, app, pw)
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
