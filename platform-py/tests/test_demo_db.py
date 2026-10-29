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


def test_existing_superuser_dsns_move_to_the_app_role() -> None:
    pairs = demo_db.superuser_dsn_pairs("S" * 20, {"ativos": "A" * 20})
    assert pairs == {
        f"postgresql://hub:{'S' * 20}@app-hub-app-db:5432/ativos": f"postgresql://ativos:{'A' * 20}@app-hub-app-db:5432/ativos"
    }


@pytest.mark.parametrize("name", ["app-env.json", "app-env.prod.json"])
def test_the_seeded_variables_use_the_app_role(name: str) -> None:
    seeded = [
        v
        for v in json.loads((ROOT / name).read_text(encoding="utf-8"))
        if v["name"].startswith("DATABASE_URL_")
    ]
    assert seeded and all(not v["value"].startswith("postgresql://hub:") for v in seeded)


@needs_compose
def test_sample_apps_connect_with_their_own_role_after_the_roles_job(tmp_path: Path) -> None:
    s = rendered(tmp_path, {"COMPOSE_PROFILES": "demo-apps"})
    job = s["app-hub-db-roles"]
    assert job["profiles"] == ["demo-apps"] and job["command"][-1] == "demo-db-roles"
    for app in demo_db.DEMO_APPS:
        svc = s[f"app-hub-app-{app}"]
        assert svc["environment"]["HUB_DB_URL"].startswith(f"postgresql://{app}:"), app
        assert svc["depends_on"]["app-hub-db-roles"]["condition"] == "service_completed_successfully"


def test_the_production_overlay_requires_each_app_password() -> None:
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for app in demo_db.DEMO_APPS:
        env = demo_db.password_env(app)
        assert f"postgresql://{app}:${{{env}:?}}@app-hub-app-db:5432/{app}" in prod
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
            await su.execute(f'DROP DATABASE IF EXISTS "{d}" WITH (FORCE)')
        await su.execute(f'DROP ROLE IF EXISTS "{app}"')
        await su.close()


@pg
async def test_the_role_owns_its_database_has_limits_and_no_more(two_dbs: tuple[str, str]) -> None:
    assert DB_URL
    app, other = two_dbs
    pw = secrets.token_hex(12)
    assert await demo_db.apply(DB_URL, {app: pw}) == [app]
    assert await demo_db.apply(DB_URL, {app: pw}) == [app]  # idempotent (every `up` runs it)
    base = DB_URL.split("://", 1)[1].split("@", 1)[1].rsplit("/", 1)[0]
    c = await asyncpg.connect(f"postgresql://{app}:{pw}@{base}/{app}")
    try:
        await c.execute("INSERT INTO itens VALUES (1)")  # the existing table now belongs to the app
        assert await c.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user") is False
        assert await c.fetchval("SHOW statement_timeout") == "5min"
    finally:
        await c.close()
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await asyncpg.connect(f"postgresql://{app}:{pw}@{base}/{other}")


def test_the_boot_migration_needs_the_real_passwords() -> None:
    env = {"APP_HUB_APP_DB_PASSWORD": "S" * 20, "APP_HUB_APP_DB_PASSWORD_ATIVOS": "A" * 20}
    assert demo_db.migration_pairs(env) == demo_db.superuser_dsn_pairs("S" * 20, {"ativos": "A" * 20})
    assert (
        demo_db.migration_pairs({"APP_HUB_APP_DB_PASSWORD_ATIVOS": "A" * 20}) == {}
    )  # dev stack: nothing to move
