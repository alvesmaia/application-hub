"""install.sh with the single Postgres: the notice, new secrets on a rerun, and the stop when an old own server exists."""

from __future__ import annotations

from pathlib import Path

import pytest
from install_helper import BASH, RECORDING_DOCKER, prod_env, run_install

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")
DEMO_PWS = [f"APP_HUB_APP_DB_PASSWORD_{a}" for a in ("ATIVOS", "FORNECEDORES", "SERVICOS", "COLETOR")]
FAKE = {"docker": RECORDING_DOCKER}  # gets past the compose check; stops at the build, after .prod.env


def test_a_new_install_has_one_database_password_and_one_per_sample_app(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes"))
    env = prod_env(tmp_path)
    assert r.returncode == 0, r.stderr[-800:]
    assert "APP_HUB_DB_PASSWORD" not in env and len(env["APP_HUB_APP_DB_PASSWORD"]) == 48
    assert len({env[k] for k in DEMO_PWS} | {env["APP_HUB_APP_DB_PASSWORD"]}) == 5  # one password per role


def test_the_option_of_two_servers_no_longer_exists(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes", "--db-mode", "split"))
    assert r.returncode == 2 and "--db-mode" in r.stderr


def test_the_notice_says_there_is_one_postgres(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--yes", "--skip-doctor"))  # stops at the compose check, after the notice
    assert "Postgres: um servidor para o hub e os apps" in r.stderr


def test_a_rerun_adds_the_new_secrets_and_keeps_the_existing(tmp_path: Path) -> None:
    assert run_install(tmp_path, ("--config-only", "--yes")).returncode == 0
    f = tmp_path / "platform-py" / ".prod.env"
    before = prod_env(tmp_path)
    f.write_text("".join(f"{k}={v}\n" for k, v in before.items() if k not in DEMO_PWS), encoding="utf-8")
    r = run_install(
        tmp_path, ("--yes", "--skip-doctor"), fakes=FAKE, env_extra={"FAKE_LOG": str(tmp_path / "log")}
    )
    after = prod_env(tmp_path)
    assert all(len(after[k]) == 48 for k in DEMO_PWS)
    assert all(after[k] == v for k, v in before.items() if k not in DEMO_PWS)
    assert (
        "APP_HUB_APP_DB_PASSWORD_ATIVOS" in r.stderr
        and after["APP_HUB_APP_DB_PASSWORD_ATIVOS"] not in r.stderr
    )


def test_an_install_that_still_has_the_old_hub_server_stops_before_building(tmp_path: Path) -> None:
    # the hub's data is in <project>-db: starting the new compose would open an empty `hub` database
    old = RECORDING_DOCKER.replace('  "inspect "*)', '  "inspect app-hub-db") exit 0 ;;\n  "inspect "*)')
    log = tmp_path / "log"
    r = run_install(
        tmp_path, ("--yes", "--skip-doctor"), fakes={"docker": old}, env_extra={"FAKE_LOG": str(log)}
    )
    assert r.returncode == 1 and "scripts/db-merge.sh" in r.stderr
    assert not log.exists()  # nothing built
