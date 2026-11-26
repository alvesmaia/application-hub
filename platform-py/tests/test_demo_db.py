"""Sample apps with a role of their own in the apps' Postgres (never the superuser `hub`)."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from test_db_mode import needs_compose, rendered

from hub.admin import demo_db
from hub.admin.config import DEV_INFRA_PASSWORDS

ROOT = Path(__file__).resolve().parents[1]
DB_URL = os.getenv("HUB_DB_TEST_URL")
pg = pytest.mark.skipif(not DB_URL, reason="HUB_DB_TEST_URL not set")


def test_a_bad_name_never_reaches_the_sql() -> None:
    with pytest.raises(ValueError, match="nome"):
        demo_db.role_sql('x"; DROP ROLE hub; --', "p" * 16)


def test_the_dev_passwords_do_not_contain_each_other() -> None:
    # the boot replaces each dev password inside the variables by plain substring: none may contain another
    devs = list(DEV_INFRA_PASSWORDS.values())
    assert all(a == b or a not in b for a in devs for b in devs)
    assert {demo_db.password_env(a) for a in demo_db.DEMO_APPS} <= set(DEV_INFRA_PASSWORDS)


def test_sample_apps_use_the_app_prefix_like_any_app() -> None:
    assert demo_db.db_of("inventory") == "app_inventory"


def test_existing_dsns_move_to_the_prefixed_role_and_database() -> None:
    a, s_ = "A" * 20, "S" * 20
    new = f"postgresql://app_inventory:{a}@app-hub-app-db:5432/app_inventory"
    assert demo_db.superuser_dsn_pairs(s_, {"inventory": a}) == {
        f"postgresql://hub:{s_}@app-hub-app-db:5432/inventory": new
    }
    assert demo_db.bare_dsn_pairs({"inventory": a}) == {
        f"postgresql://inventory:{a}@app-hub-app-db:5432/inventory": new
    }
    assert all(old not in v for old, v in demo_db.bare_dsn_pairs({"inventory": a}).items())  # idempotent


@pytest.mark.parametrize("name", ["app-env.json", "app-env.prod.json"])
def test_the_seeded_variables_use_the_app_role(name: str) -> None:
    seeded = [
        v
        for v in json.loads((ROOT / name).read_text(encoding="utf-8"))
        if v["name"].startswith("DATABASE_URL_")
    ]
    assert seeded and all(not v["value"].startswith("postgresql://hub:") for v in seeded)
    for v in seeded:
        app = v["bindings"][0]["app"]
        assert v["value"].startswith(f"postgresql://app_{app}:") and v["value"].endswith(f"/app_{app}"), v[
            "name"
        ]


@needs_compose
def test_sample_apps_connect_with_their_own_role_after_the_roles_job(tmp_path: Path) -> None:
    s = rendered(tmp_path, {"COMPOSE_PROFILES": "demo-apps"})
    job = s["app-hub-db-roles"]
    assert job["profiles"] == ["demo-apps"] and job["command"][-1] == "demo-db-roles"
    for app in demo_db.DEMO_APPS:
        svc = s[f"app-hub-app-{app}"]
        url = svc["environment"]["HUB_DB_URL"]
        assert url.startswith(f"postgresql://app_{app}:") and url.endswith(f"/app_{app}"), app
        assert svc["depends_on"]["app-hub-db-roles"]["condition"] == "service_completed_successfully"


def test_the_production_overlay_requires_each_app_password() -> None:
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for app in demo_db.DEMO_APPS:
        env = demo_db.password_env(app)
        assert f"postgresql://app_{app}:${{{env}:?}}@app-hub-app-db:5432/app_{app}" in prod
        assert f"{env}=<openssl rand -hex 24>" in (ROOT / "prod.env.example").read_text(encoding="utf-8")


@pytest.fixture
async def two_dbs() -> AsyncIterator[tuple[str, str]]:
    """An app database with a table made by the superuser (an existing install) and a neighbour database."""
    assert DB_URL
    app, other = f"t_{secrets.token_hex(4)}", f"t_{secrets.token_hex(4)}"
    su = await asyncpg.connect(DB_URL)
    try:
        for d in (app, other):
            await su.execute(f'CREATE DATABASE "{d}"')
        await su.execute(f'REVOKE ALL ON DATABASE "{other}" FROM PUBLIC')
        c = await asyncpg.connect(DB_URL.rsplit("/", 1)[0] + f"/{app}")
        await c.execute("CREATE TABLE itens (id int)")
        await c.close()
        yield app, other
    finally:
        for d in (app, other):
            for name in (d, f"app_{d}"):
                await su.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        for role in (app, f"app_{app}", f"hub_read_{app}", f"hub_read_app_{app}", f"ro_{app}"):
            await su.execute(f'DROP ROLE IF EXISTS "{role}"')
        await su.close()


@pg
async def test_the_role_owns_its_database_has_limits_and_no_more(two_dbs: tuple[str, str]) -> None:
    assert DB_URL
    app, other = two_dbs
    pw = secrets.token_hex(12)
    assert await demo_db.apply(DB_URL, {app: pw}) == [app]
    assert await demo_db.apply(DB_URL, {app: pw}) == [app]  # idempotent (every `up` runs it)
    base = DB_URL.split("://", 1)[1].split("@", 1)[1].rsplit("/", 1)[0]
    role = f"app_{app}"
    c = await asyncpg.connect(f"postgresql://{role}:{pw}@{base}/{role}")
    try:
        await c.execute("INSERT INTO itens VALUES (1)")  # the existing table now belongs to the app
        assert await c.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user") is False
        assert await c.fetchval("SHOW statement_timeout") == "5min"
    finally:
        await c.close()
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await asyncpg.connect(f"postgresql://{role}:{pw}@{base}/{other}")


@pg
async def test_an_existing_install_moves_to_the_prefixed_names_keeping_data_and_read_accounts(
    two_dbs: tuple[str, str],
) -> None:
    """Before 0.8: database and role `<id>`, a read group `hub_read_<id>` and a read account that lists `<id>`."""
    assert DB_URL
    app, _ = two_dbs
    pw = secrets.token_hex(12)
    su = await asyncpg.connect(DB_URL)
    try:
        await su.execute(f"CREATE ROLE \"{app}\" LOGIN PASSWORD '{pw}'")
        await su.execute(f"COMMENT ON ROLE \"{app}\" IS 'hub-managed:demo={app}'")
        await su.execute(f'ALTER DATABASE "{app}" OWNER TO "{app}"')
        await su.execute(f'CREATE ROLE "hub_read_{app}" NOLOGIN')
        meta = json.dumps({"owner": "u1", "dbs": [app, "outro"]})
        await su.execute(f'CREATE ROLE "ro_{app}" LOGIN; COMMENT ON ROLE "ro_{app}" IS \'{meta}\'')
        await su.execute(f'GRANT "hub_read_{app}" TO "ro_{app}"')
        old = await asyncpg.connect(DB_URL.rsplit("/", 1)[0] + f"/{app}")
        await old.execute("INSERT INTO itens VALUES (42)")
        await old.close()
        await demo_db.apply(DB_URL, {app: pw})
        await demo_db.apply(DB_URL, {app: pw})  # a second `up` changes nothing
        names = {r[0] for r in await su.fetch("SELECT datname FROM pg_database")}
        roles = {r[0] for r in await su.fetch("SELECT rolname FROM pg_roles")}
        assert f"app_{app}" in names and app not in names
        assert f"app_{app}" in roles and app not in roles
        assert f"hub_read_app_{app}" in roles and f"hub_read_{app}" not in roles
        comment = await su.fetchval(
            "SELECT shobj_description(oid, 'pg_authid') FROM pg_roles WHERE rolname = $1", f"ro_{app}"
        )
        assert json.loads(comment)["dbs"] == [f"app_{app}", "outro"]
    finally:
        await su.close()
    base = DB_URL.split("://", 1)[1].split("@", 1)[1].rsplit("/", 1)[0]
    c = await asyncpg.connect(f"postgresql://app_{app}:{pw}@{base}/app_{app}")
    try:
        assert await c.fetchval("SELECT max(id) FROM itens") == 42  # the data came along
    finally:
        await c.close()


def test_the_boot_migration_rewrites_the_stored_connections() -> None:
    env = {"APP_HUB_APP_DB_PASSWORD": "S" * 20, "APP_HUB_APP_DB_PASSWORD_INVENTORY": "A" * 20}
    pairs = demo_db.migration_pairs(env)
    assert demo_db.superuser_dsn_pairs("S" * 20, {"inventory": "A" * 20}).items() <= pairs.items()
    assert demo_db.bare_dsn_pairs({"inventory": "A" * 20}).items() <= pairs.items()
    # dev stack (seeded dev passwords, no superuser password): only the move to the prefixed names
    dev = demo_db.migration_pairs({})
    pw = DEV_INFRA_PASSWORDS[demo_db.password_env("inventory")]
    assert dev == demo_db.bare_dsn_pairs(
        {a: DEV_INFRA_PASSWORDS[demo_db.password_env(a)] for a in demo_db.DEMO_APPS}
    )
    assert f"postgresql://inventory:{pw}@app-hub-app-db:5432/inventory" in dev
