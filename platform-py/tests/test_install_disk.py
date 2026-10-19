"""install.sh: where the hub keeps its data (the disks the user can write to) and, on rootless Podman, its images."""

from __future__ import annotations

from pathlib import Path

import pytest
from install_helper import BASH, ROOT, prod_env, run_install, sh_path

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

# df -PTk of a server with a small /opt (engine store) and a 1 TB /data; tmpfs/overlay never show up
FAKE_DF = r"""
cat <<EOT
Filesystem     Type     1024-blocks      Used  Available Capacity Mounted on
/dev/sda2      xfs         52428800  44564480    7864320      85% $FAKE_ROOT/opt
/dev/sdb1      xfs       1073741824   1048576 1072693248       1% $FAKE_ROOT/data
tmpfs          tmpfs        8000000         0    8000000       0% $FAKE_ROOT/run/user/1000
overlay        overlay     52428800  44564480    7864320      85% $FAKE_ROOT/var/lib/containers/overlay
EOT
"""


def disks(tmp_path: Path) -> dict[str, str]:
    root = tmp_path / "root"
    for d in ("opt", "data", "run/user/1000"):
        (root / d).mkdir(parents=True)
    return {"FAKE_ROOT": sh_path(root)}


def test_lists_the_writable_disks_and_recommends_the_one_with_more_space(tmp_path: Path) -> None:
    base = ["", "", "", "", "", "", "n", "n"]  # instance questions, sample apps: no, network: no
    env = disks(tmp_path)
    engine = (
        'case "$1" in info) echo "$FAKE_ROOT/opt/containers/storage" ;; *) exit 1 ;; esac'  # /opt: 7 GB free
    )
    r = run_install(
        tmp_path,
        ("--config-only",),
        answers=[*base, "", "", ""],  # disk: Enter (= recommended) · advanced: no · write: yes
        fakes={"df": FAKE_DF, "docker": engine},
        env_extra=env,
    )
    assert r.returncode == 0, r.stderr
    out = r.stderr
    assert (
        f"{env['FAKE_ROOT']}/data" in out
        and "1023 GB" in out
        and "/run/user" not in out
        and "overlay" not in out
    )
    assert "recomendado" in out.split(f"{env['FAKE_ROOT']}/data", 1)[1].split("\n", 1)[0]
    e = prod_env(tmp_path)
    data = f"{env['FAKE_ROOT']}/data/app-hub"
    assert e["HUB_DB_DIR"] == f"{data}/db" and e["HUB_APPDB_DIR"] == f"{data}/app-db"
    assert e["HUB_DATA_HOST_DIR"] == f"{data}/data" and e["HUB_OBJSTORE_DIR"] == f"{data}/objstore"


def test_choosing_the_engine_volumes_keeps_the_previous_behavior(tmp_path: Path) -> None:
    base = ["", "", "", "", "", "", "n", "n"]
    r = run_install(
        tmp_path,
        ("--config-only",),
        answers=[*base, "0", "", ""],
        fakes={"df": FAKE_DF},
        env_extra=disks(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    for k in ("HUB_DB_DIR", "HUB_APPDB_DIR", "HUB_DATA_HOST_DIR", "HUB_OBJSTORE_DIR"):
        assert k not in prod_env(tmp_path), k


def test_data_dir_flag_creates_the_folders(tmp_path: Path) -> None:
    target = tmp_path / "grande"
    r = run_install(tmp_path, ("--config-only", "--yes", "--data-dir", sh_path(target)))
    assert r.returncode == 0, r.stderr
    assert prod_env(tmp_path)["HUB_DB_DIR"] == f"{sh_path(target)}/db"
    assert all((target / d).is_dir() for d in ("db", "app-db", "data", "objstore"))


def test_a_data_dir_that_cannot_be_written_is_refused(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes", "--data-dir", "relativo/sem-barra"))
    assert r.returncode == 2 and "--data-dir" in r.stderr and "relativo/sem-barra" in r.stderr


def test_selinux_enforcing_labels_the_host_folders(tmp_path: Path) -> None:
    r = run_install(
        tmp_path,
        ("--config-only", "--yes", "--data-dir", sh_path(tmp_path / "d")),
        fakes={"getenforce": "echo Enforcing"},
    )
    assert r.returncode == 0 and prod_env(tmp_path)["HUB_BIND_OPTS"] == ":Z"
    r = run_install(tmp_path / "b", ("--config-only", "--yes", "--data-dir", sh_path(tmp_path / "e")))
    assert "HUB_BIND_OPTS" not in prod_env(tmp_path / "b")


def test_compose_mounts_the_host_folders_with_the_selinux_option() -> None:
    hub = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    apps = (ROOT / "docker-compose.apps.yml").read_text(encoding="utf-8")
    assert "${HUB_DB_DIR:-app-hub-db-data}:/var/lib/postgresql/data${HUB_BIND_OPTS:-}" in hub
    assert "${HUB_DATA_HOST_DIR:-app-hub-data}:/data${HUB_BIND_OPTS:-}" in hub
    assert "${HUB_OBJSTORE_DIR:-app-hub-objstore-data}:/data${HUB_BIND_OPTS:-}" in hub
    assert "${HUB_APPDB_DIR:-app-hub-app-db-data}:/var/lib/postgresql/data${HUB_BIND_OPTS:-}" in apps


def test_engine_store_is_only_for_rootless_podman(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--yes", "--skip-doctor", "--engine-store", sh_path(tmp_path / "imgs")))
    assert r.returncode == 2 and "--engine-store" in r.stderr and "Podman rootless" in r.stderr
