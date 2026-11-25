"""install.sh: read access to the apps' database from other machines (dashboards, DBeaver), on a new install by
flags or by the advanced question, and on an existing install (rerun) by flags or one new question."""

from __future__ import annotations

from pathlib import Path

import pytest
from install_helper import BASH, prod_env, run_install

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")
ON = (
    "--db-public-port",
    "5433",
    "--db-allowed-cidrs",
    "10.0.0.0/8,192.168.1.0/24",
    "--db-public-host",
    "db.empresa.com",
)


def test_flags_turn_it_on(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes", *ON))
    assert r.returncode == 0, r.stderr
    env = prod_env(tmp_path)
    assert (
        env["HUB_DB_PUBLIC"] == "1"
        and env["HUB_DB_PUBLIC_PORT"] == "5433"
        and env["HUB_DB_PUBLIC_BIND"] == "0.0.0.0"  # noqa: S104 - the expected value
    )
    assert (
        env["HUB_DB_ALLOWED_CIDRS"] == "10.0.0.0/8,192.168.1.0/24"
        and env["HUB_DB_PUBLIC_HOST"] == "db.empresa.com"
    )


def test_off_by_default_and_networks_are_required(tmp_path: Path) -> None:
    assert run_install(tmp_path, ("--config-only", "--yes")).returncode == 0
    assert "HUB_DB_PUBLIC" not in prod_env(tmp_path)
    r = run_install(tmp_path / "b", ("--config-only", "--yes", "--db-public-port", "5432"))
    assert r.returncode == 2 and "--db-allowed-cidrs" in r.stderr
    r = run_install(
        tmp_path / "c", ("--config-only", "--yes", "--db-public-port", "5432", "--db-allowed-cidrs", "x;rm")
    )
    assert r.returncode == 2 and "inválido" in r.stderr


def test_turning_it_off_goes_back_to_the_loopback_port(tmp_path: Path) -> None:
    assert run_install(tmp_path, ("--config-only", "--yes", *ON)).returncode == 0
    r = run_install(tmp_path, ("--config-only", "--yes", "--no-db-public"))
    env = prod_env(tmp_path)
    assert r.returncode == 0 and env["HUB_DB_PUBLIC"] == "0"
    assert "HUB_DB_PUBLIC_BIND" not in env and "HUB_DB_PUBLIC_PORT" not in env


def test_an_existing_install_is_asked_once(tmp_path: Path) -> None:
    """Rerun keeps .prod.env without questions, except this option, new in this version (asked while unanswered)."""
    assert run_install(tmp_path, ("--config-only", "--yes")).returncode == 0
    r = run_install(
        tmp_path, (), answers=["n", "n"]
    )  # "no" to the database question, "no" to the install consent
    assert "Opção nova desta versão" in r.stdout and "Acesso de leitura ao banco" in r.stdout + r.stderr
    assert "cancelado" in r.stdout + r.stderr  # consent refused: nothing written
    env_file = tmp_path / "platform-py" / ".prod.env"
    env_file.write_text(
        env_file.read_text(encoding="utf-8") + "HUB_DB_PUBLIC=0\n", encoding="utf-8", newline="\n"
    )
    again = run_install(tmp_path, (), answers=["n"])
    assert "Opção nova desta versão" not in again.stdout  # already answered: not asked again
