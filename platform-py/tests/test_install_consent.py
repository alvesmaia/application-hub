"""install.sh says what it will do and only acts after an explicit yes (default no; --yes = consent given)."""

from __future__ import annotations

from pathlib import Path

import pytest
from install_helper import BASH, run_install

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

FLAGS = ("--admin-email", "ops@exemplo.com", "--port", "8095")


def test_banner_lists_what_the_install_does(tmp_path: Path) -> None:
    r = run_install(tmp_path, FLAGS, answers=["n"])
    for needle in ("app-hub-*", "socket", "8095", ".secrets", "Não usa sudo"):
        assert needle in r.stderr, needle


def test_enter_or_no_cancels_before_anything_is_written(tmp_path: Path) -> None:
    for i, answer in enumerate(("", "n")):
        r = run_install(tmp_path / str(i), FLAGS, answers=[answer])
        assert r.returncode == 1 and "cancelado" in r.stderr
        assert not (tmp_path / str(i) / "platform-py" / ".prod.env").exists()


def test_yes_continues_and_the_consent_is_logged(tmp_path: Path) -> None:
    r = run_install(tmp_path, FLAGS, answers=["s"])
    assert (
        "cancelado" not in r.stderr and "Docker Compose v2 não encontrado" in r.stderr
    )  # went on (fake engine)
    r = run_install(tmp_path / "y", (*FLAGS, "--yes"))
    assert "consentimento dado por --yes" in r.stderr and "Docker Compose v2 não encontrado" in r.stderr


def test_without_a_terminal_it_asks_for_yes_instead_of_assuming(tmp_path: Path) -> None:
    r = run_install(tmp_path, FLAGS)  # no INSTALL_TTY, stdin is not a terminal
    assert r.returncode == 1 and "--yes" in r.stderr and "Docker Compose" not in r.stderr


def test_interactive_mode_ends_in_the_same_consent(tmp_path: Path) -> None:
    base = [
        "",
        "",
        "",
        "",
        "",
        "",
        "n",
        "",
        "",
    ]  # instance questions, network and advanced options: Enter (= no)
    r = run_install(tmp_path, (), answers=[*base, ""])  # consent: Enter (= no)
    assert r.returncode == 1 and "cancelado" in r.stderr and "Não usa sudo" in r.stderr


def test_config_only_writes_the_file_without_the_consent(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", *FLAGS))
    assert r.returncode == 0 and "Não usa sudo" not in r.stderr


def test_help_prints_the_whole_header_including_the_consent(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--help",))
    assert r.returncode == 0 and "Continuar? (s/N)" in r.stdout and "set -euo" not in r.stdout
