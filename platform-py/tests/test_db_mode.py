"""One Postgres for the hub and the apps: the `hub` database next to the apps' ones in app-hub-app-db."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hub.ops.backups import backup_role_sql
from hub.ops.model import OpsError

ROOT = Path(__file__).resolve().parents[1]
DOCKER = shutil.which("docker") or "docker"
needs_compose = pytest.mark.skipif(
    shutil.which("docker") is None
    or subprocess.run([DOCKER, "compose", "version"], capture_output=True, check=False).returncode != 0,  # noqa: S603
    reason="docker compose ausente",
)


def rendered(tmp_path: Path, env: dict[str, str]) -> dict[str, Any]:
    """`docker compose config` of a copy (outside the repo: no .dev.env/.prod.env read) with `env` set."""
    for f in ("docker-compose.yml", "docker-compose.apps.yml"):
        shutil.copy(ROOT / f, tmp_path / f)
    p = subprocess.run(  # noqa: S603
        [DOCKER, "compose", "-f", "docker-compose.yml", "-f", "docker-compose.apps.yml", "config", "--format", "json"],
        cwd=tmp_path, env={**_base_env(), **env}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert p.returncode == 0, p.stderr[-400:]
    return json.loads(p.stdout)["services"]


def _base_env() -> dict[str, str]:
    """The test's environment minus the stack settings (a developer's HUB_*/APP_HUB_* would change the render)."""
    return {k: v for k, v in os.environ.items() if not k.startswith(("HUB_", "APP_HUB_", "COMPOSE_"))}


@needs_compose
def test_the_hub_database_lives_in_the_single_server(tmp_path: Path) -> None:
    s = rendered(tmp_path, {})
    assert "app-hub-db" not in s  # one Postgres only: app-hub-app-db
    web = s["app-hub-web"]
    for var in ("HUB_DB_URL", "HUB_STATE_DB_URL"):
        assert web["environment"][var] == "postgresql://hub:hubpy-app-dev@app-hub-app-db:5432/hub", var
    assert "app-hub-app-db" in web["depends_on"]


@needs_compose
def test_the_shared_server_keeps_connections_for_the_hub(tmp_path: Path) -> None:
    # the apps' roles are not superusers: when they use every normal slot, the hub (superuser `hub`) still connects
    cmd = " ".join(rendered(tmp_path, {})["app-hub-app-db"]["command"])
    assert "max_connections=200" in cmd and "superuser_reserved_connections=30" in cmd


def test_the_production_overlay_uses_the_single_server() -> None:
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for var in ("HUB_DB_URL", "HUB_STATE_DB_URL"):
        assert f"{var}: postgresql://hub:${{APP_HUB_APP_DB_PASSWORD:?}}@app-hub-app-db:5432/hub" in prod
    assert "app-hub-db:" not in prod and "APP_HUB_DB_PASSWORD" not in prod


def test_an_app_backup_never_reaches_the_hub_database() -> None:
    # in the shared server the hub's own database sits next to the apps': an app pointed at it must not dump it
    with pytest.raises(OpsError, match="hub"):
        backup_role_sql("hub", "a" * 24)


DB_URL = os.getenv("HUB_DB_TEST_URL")


@pytest.mark.skipif(not DB_URL, reason="HUB_DB_TEST_URL not set")
async def test_the_hub_closes_its_own_database_to_the_apps_roles() -> None:
    # on the shared server the `hub` database is born with CONNECT for PUBLIC (POSTGRES_DB): the hub takes it away
    import asyncpg

    from hub.state import Config, StateDb

    assert DB_URL
    name = f"t_{os.urandom(4).hex()}"
    su = await asyncpg.connect(DB_URL)
    try:
        await su.execute(f'CREATE DATABASE "{name}"')
        db = await StateDb.connect(Config(DB_URL.rsplit("/", 1)[0] + f"/{name}", "hub_state", 2, "r"))
        await db.close()
        assert await su.fetchval(f"SELECT has_database_privilege('public', '{name}', 'CONNECT')") is False
    finally:
        await su.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await su.close()
