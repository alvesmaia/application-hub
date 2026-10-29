"""scripts/db-merge.sh: the hub's database leaves the old own server (`<project>-db`) for the single one."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from install_helper import BASH

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "db-merge.sh"
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")
real = pytest.mark.skipif(
    os.getenv("HUB_MERGE_TEST") != "1", reason="HUB_MERGE_TEST=1 (real Postgres containers)"
)
P = "app-hub-mergetest"
DOCKER = shutil.which("docker") or "docker"


def merge(*args: str, path: str | None = None, dumps: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        **({"PATH": path} if path else {}),
        **({"HUB_MERGE_DIR": str(dumps)} if dumps else {}),
    }
    return subprocess.run(  # noqa: S603
        [str(BASH), str(SCRIPT), *args], env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=180,
    )  # fmt: skip


def fake_docker(tmp_path: Path, body: str) -> str:
    (tmp_path / "docker").write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    (tmp_path / "docker").chmod(0o755)
    return f"{tmp_path}:{Path(str(BASH)).parent}:/usr/bin:/bin"


def test_an_install_with_one_server_has_nothing_to_merge(tmp_path: Path) -> None:
    r = merge("--project", P, "--yes", path=fake_docker(tmp_path, "exit 1"))  # no <project>-db
    assert r.returncode == 0 and "nada a fundir" in r.stdout


def test_without_a_terminal_it_needs_yes(tmp_path: Path) -> None:
    r = merge("--project", P, path=fake_docker(tmp_path, "exit 0"))
    assert r.returncode == 1 and "--yes" in r.stderr


def sh(*cmd: str) -> str:
    return subprocess.run([DOCKER, *cmd], capture_output=True, text=True, check=True).stdout.strip()  # noqa: S603


def psql(container: str, sql: str, db: str = "hub") -> str:
    return sh("exec", container, "psql", "-U", "hub", "-d", db, "-tAc", sql)


@pytest.fixture
def old_layout() -> Iterator[None]:
    """Two servers as the installs before 0.3: the hub's data in <P>-db, an empty `hub` database in <P>-app-db."""
    names = (f"{P}-db", f"{P}-app-db")
    for n in names:
        subprocess.run([DOCKER, "rm", "-f", "-v", n], capture_output=True, check=False)  # noqa: S603 - a leftover run
        sh("run", "-d", "--name", n, "-e", "POSTGRES_USER=hub", "-e", "POSTGRES_PASSWORD=x",
           "-e", "POSTGRES_DB=hub", "postgres:16-alpine")  # fmt: skip
    try:
        for n in names:
            for _ in range(60):
                if subprocess.run([DOCKER, "exec", n, "pg_isready", "-U", "hub", "-d", "hub"],  # noqa: S603
                                  capture_output=True, check=False).returncode == 0:  # fmt: skip
                    break
                time.sleep(1)
        time.sleep(2)  # the image restarts once after the init scripts
        psql(f"{P}-db", "CREATE SCHEMA hub_state; CREATE TABLE hub_state.sessions (id int); "
             "INSERT INTO hub_state.sessions SELECT generate_series(1, 25)")  # fmt: skip
        yield
    finally:
        for n in (*names, *sh("ps", "-aq", "--filter", f"name={P}-db-merged").split()):
            subprocess.run([DOCKER, "rm", "-f", "-v", n], capture_output=True, check=False)  # noqa: S603


@real
def test_the_hub_database_moves_and_the_old_server_is_kept_for_rollback(
    old_layout: None, tmp_path: Path
) -> None:
    r = merge("--project", P, "--yes", dumps=tmp_path)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    assert psql(f"{P}-app-db", "SELECT count(*) FROM hub_state.sessions") == "25"
    kept = sh("ps", "-a", "--filter", f"name={P}-db-merged", "--format", "{{.Names}} {{.State}}")
    assert kept.startswith(f"{P}-db-merged-") and kept.endswith("exited")
    assert "up -d --no-deps app-hub-web" in r.stdout and len(list(tmp_path.glob("hub-*.dump"))) == 1
    again = merge("--project", P, "--yes", dumps=tmp_path)  # the old name is gone: nothing more to do
    assert again.returncode == 0 and "nada a fundir" in again.stdout


@real
def test_a_target_already_in_use_is_never_overwritten(old_layout: None, tmp_path: Path) -> None:
    psql(f"{P}-app-db", "CREATE TABLE ja_em_uso (id int)")
    r = merge("--project", P, "--yes", dumps=tmp_path)
    assert r.returncode == 1 and "já tem tabelas" in r.stderr
    assert sh("inspect", "-f", "{{.State.Running}}", f"{P}-db") == "true"  # nothing changed on the old one
