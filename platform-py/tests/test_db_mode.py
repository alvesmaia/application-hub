"""One Postgres for the hub and the apps (default of new installs) or one each (`split`): the compose renders both."""

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
SINGLE = {"HUB_CORE_DB_HOST": "app-hub-app-db", "HUB_CORE_DB_SCALE": "0"}
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
def test_without_the_new_settings_an_existing_install_keeps_two_servers(tmp_path: Path) -> None:
    s = rendered(tmp_path, {})
    assert s["app-hub-db"].get("scale", 1) == 1
    assert "@app-hub-db:5432/hub" in s["app-hub-web"]["environment"]["HUB_DB_URL"]


@needs_compose
def test_single_mode_puts_the_hub_database_in_the_apps_server(tmp_path: Path) -> None:
    s = rendered(tmp_path, SINGLE)
    assert s["app-hub-db"]["scale"] == 0  # no container: one Postgres only
    web = s["app-hub-web"]
    for var in ("HUB_DB_URL", "HUB_STATE_DB_URL"):
        assert web["environment"][var].endswith("@app-hub-app-db:5432/hub"), var
    assert "app-hub-app-db" in web["depends_on"]


@needs_compose
def test_the_shared_server_keeps_connections_for_the_hub(tmp_path: Path) -> None:
    # the apps' roles are not superusers: when they use every normal slot, the hub (superuser `hub`) still connects
    cmd = " ".join(rendered(tmp_path, SINGLE)["app-hub-app-db"]["command"])
    assert "max_connections=200" in cmd and "superuser_reserved_connections=30" in cmd


def test_the_production_overlay_follows_the_same_host() -> None:
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    for var in ("HUB_DB_URL", "HUB_STATE_DB_URL"):
        assert (
            f"{var}: postgresql://hub:${{APP_HUB_DB_PASSWORD:?}}@${{HUB_CORE_DB_HOST:-app-hub-db}}:5432/hub"
            in prod
        )


def test_an_app_backup_never_reaches_the_hub_database() -> None:
    # in the shared server the hub's own database sits next to the apps': an app pointed at it must not dump it
    with pytest.raises(OpsError, match="hub"):
        backup_role_sql("hub", "a" * 24)
