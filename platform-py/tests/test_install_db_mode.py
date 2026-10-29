"""install.sh --db-mode single|split (default single) and the interactive question; a rerun completes new secrets."""

from __future__ import annotations

from pathlib import Path

import pytest
from install_helper import BASH, RECORDING_DOCKER, prod_env, run_install

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")
DEMO_PWS = [f"APP_HUB_APP_DB_PASSWORD_{a}" for a in ("ATIVOS", "FORNECEDORES", "SERVICOS", "COLETOR")]
# e-mail, port, bind, URL, project, subnet, sample apps, network · advanced: s + 12 Enters up to the build CPUs
TO_DB_QUESTION = ["", "", "", "", "", "", "n", "", "s", *[""] * 12]


def test_by_default_the_hub_database_goes_to_the_apps_server(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes"))
    env = prod_env(tmp_path)
    assert r.returncode == 0, r.stderr[-800:]
    assert env["HUB_DB_MODE"] == "single" and env["HUB_CORE_DB_SCALE"] == "0"
    assert env["HUB_CORE_DB_HOST"] == "app-hub-app-db"
    assert (
        env["APP_HUB_DB_PASSWORD"] == env["APP_HUB_APP_DB_PASSWORD"] and len(env["APP_HUB_DB_PASSWORD"]) == 48
    )
    assert len({env[k] for k in DEMO_PWS} | {env["APP_HUB_APP_DB_PASSWORD"]}) == 5  # one password per role


def test_split_keeps_one_server_each_with_its_own_password(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes", "--db-mode", "split"))
    env = prod_env(tmp_path)
    assert r.returncode == 0 and env["HUB_DB_MODE"] == "split"
    assert "HUB_CORE_DB_SCALE" not in env and "HUB_CORE_DB_HOST" not in env
    assert env["APP_HUB_DB_PASSWORD"] != env["APP_HUB_APP_DB_PASSWORD"]
    assert "--db-mode split" in (tmp_path / "platform-py" / ".secrets" / "install-cmd.txt").read_text(
        encoding="utf-8"
    )


def test_an_unknown_mode_is_refused(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes", "--db-mode", "dois"))
    assert r.returncode == 2 and "single" in r.stderr and "split" in r.stderr


def test_the_interactive_install_asks_the_mode_in_the_advanced_options(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only",), [*TO_DB_QUESTION, "split", ""])
    assert r.returncode == 0, r.stderr[-800:]
    assert "Postgres" in r.stderr and prod_env(tmp_path)["HUB_DB_MODE"] == "split"


def test_the_notice_says_how_many_postgres_servers(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--yes", "--skip-doctor"))  # stops at the compose check, after the notice
    assert "Postgres: um servidor para o hub e os apps" in r.stderr


def test_a_rerun_adds_the_new_secrets_and_keeps_the_existing(tmp_path: Path) -> None:
    assert run_install(tmp_path, ("--config-only", "--yes")).returncode == 0
    f = tmp_path / "platform-py" / ".prod.env"
    before = prod_env(tmp_path)
    f.write_text("".join(f"{k}={v}\n" for k, v in before.items() if k not in DEMO_PWS), encoding="utf-8")
    fake = {"docker": RECORDING_DOCKER}  # gets past the compose check; stops at the build, after .prod.env
    r = run_install(
        tmp_path, ("--yes", "--skip-doctor"), fakes=fake, env_extra={"FAKE_LOG": str(tmp_path / "log")}
    )
    after = prod_env(tmp_path)
    assert all(len(after[k]) == 48 for k in DEMO_PWS)
    assert all(after[k] == v for k, v in before.items() if k not in DEMO_PWS)
    assert (
        "APP_HUB_APP_DB_PASSWORD_ATIVOS" in r.stderr
        and after["APP_HUB_APP_DB_PASSWORD_ATIVOS"] not in r.stderr
    )


def test_a_rerun_never_switches_the_mode_of_an_installed_hub(tmp_path: Path) -> None:
    assert run_install(tmp_path, ("--config-only", "--yes")).returncode == 0
    r = run_install(tmp_path, ("--yes", "--skip-doctor", "--db-mode", "split"))
    assert r.returncode == 2 and "dump" in r.stderr and prod_env(tmp_path)["HUB_DB_MODE"] == "single"
