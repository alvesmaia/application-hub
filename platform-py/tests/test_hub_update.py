"""hub_update.py: the running image gets a tag before the build (in containerd, without a tag it vanishes from the ID)."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

spec = importlib.util.spec_from_file_location(
    "hub_update", Path(__file__).parents[1] / "scripts" / "hub_update.py"
)
assert spec is not None and spec.loader is not None
hu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hu)


def test_build_tags_running_image_before_building(monkeypatch, tmp_path):
    calls: list[tuple[str, ...]] = []
    tagged: set[str] = set()

    def run(*args, check=True, quiet=False, env=None):
        calls.append(args)
        if args[:2] == ("docker", "tag"):
            tagged.add(args[3])
        if args[:3] == ("docker", "image", "inspect"):
            return subprocess.CompletedProcess(args, 0, "sha256:new\n", "")
        if "build" in args and not tagged:  # build without a tag: the old image stops existing by ID
            raise AssertionError("build antes de proteger a imagem atual")
        return subprocess.CompletedProcess(args, 0, "", "")

    main_c = {
        "Image": "sha256:old",
        "State": {"Status": "running"},
        "Config": {"Labels": {"com.docker.compose.service": "app-hub-web"}},
    }
    monkeypatch.setattr(hu, "run", run)
    monkeypatch.setattr(hu, "inspect", lambda n: None if n == "app-hub-db" else main_c)
    monkeypatch.setattr(hu, "compose_cmd", lambda c, *_: ("docker", "compose"))
    backups: list[str] = []
    monkeypatch.setattr(hu, "backup", lambda proj, image, dest: backups.append(image))
    monkeypatch.setattr(hu, "swap", lambda *a: "x")
    monkeypatch.setattr(hu, "watch", lambda *a: True)
    monkeypatch.setattr(sys, "argv", ["hub_update", "--build", "--backup-dir", str(tmp_path)])
    hu.main()
    assert backups == ["app-hub-web:pre-update"]
    assert ("docker", "tag", "app-hub-web:pre-update", "app-hub-web:rollback") in calls


def test_backup_tolerates_files_changing_during_tar(monkeypatch, tmp_path):
    def run(*args, check=True, quiet=False):
        assert not check or args[1] != "run", "tar do volume não pode reprovar por arquivo alterado"
        return subprocess.CompletedProcess(args, 1 if "tar" in args else 0, "", "file changed as we read it")

    monkeypatch.setattr(hu, "run", run)
    monkeypatch.setattr(hu, "inspect", lambda n: None)
    hu.backup("app-hub", "app-hub-web:pre-update", tmp_path)


def test_build_uses_the_code_of_this_checkout_not_the_one_that_created_the_container(tmp_path):
    old = r"C:\old\platform-py"
    main = {"Config": {"Labels": {
        "com.docker.compose.project": "app-hub",
        "com.docker.compose.project.working_dir": old,
        "com.docker.compose.project.config_files": rf"{old}\docker-compose.yml,{old}\docker-compose.prod.yml",
        "com.docker.compose.project.environment_file": rf"{old}\.prod.env",
    }}}  # fmt: skip
    cmd = hu.compose_cmd(main, root=tmp_path)
    assert cmd[cmd.index("--project-directory") + 1] == str(tmp_path)
    assert str(tmp_path / "docker-compose.prod.yml") in cmd and str(tmp_path / ".prod.env") in cmd
    assert not any(old in c for c in cmd)
    assert old in hu.compose_cmd(main)  # without root: the container's own folder (rollback/old behavior)


def test_build_gets_the_network_settings_of_prod_env(tmp_path):
    # corporate network (install.sh --proxy/--ca-file/mirrors): the rebuild needs the same build args as the install
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "ca.pem").write_text("-----BEGIN CERTIFICATE-----\nX\n", encoding="utf-8")
    env_file = tmp_path / ".prod.env"
    env_file.write_text(
        "APP_HUB_DB_PASSWORD=nao-vaza\nHUB_PROXY=http://proxy.empresa.local:8080\nHUB_NO_PROXY=.empresa.local\n"
        "HUB_CA_FILE=./.secrets/ca.pem\nHUB_PYPI_INDEX=https://repo.empresa.local/simple\n"
        "HUB_IMAGE_PREFIX=docker.repo.empresa.local/library/\n",
        encoding="utf-8",
    )
    env = hu.build_env([str(env_file)])
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://proxy.empresa.local:8080"
    assert env["NO_PROXY"] == "localhost,127.0.0.1,.empresa.local"
    assert env["EXTRA_CA_PEM"].startswith("-----BEGIN CERTIFICATE-----")
    assert env["PIP_INDEX_URL"] == env["UV_DEFAULT_INDEX"] == "https://repo.empresa.local/simple"
    assert env["IMAGE_PREFIX"] == "docker.repo.empresa.local/library/"
    assert "APP_HUB_DB_PASSWORD" not in env  # only the build args, never the secrets
    assert hu.build_env([str(tmp_path / "missing.env")]) == {}


def test_backup_reads_the_hub_data_from_the_container_mounts_not_a_volume_name(monkeypatch, tmp_path):
    # install.sh --data-dir puts /data in a host folder: `-v <proj>-data:/d` would back up a new, empty volume
    calls: list[tuple[str, ...]] = []

    def run(*args, check=True, quiet=False):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(hu, "run", run)
    monkeypatch.setattr(hu, "inspect", lambda n: None)
    hu.backup("app-hub", "app-hub-web:pre-update", tmp_path)
    tar = next(c for c in calls if "tar" in c)
    assert "--volumes-from" in tar and "app-hub-web:ro" in tar and "/data" in tar
    assert not any(a.startswith("app-hub-data:") for a in tar)


def test_watch_probes_an_edge_that_serves_https(monkeypatch):
    # install.sh --tls-cert: the edge port speaks TLS, a plain http probe would fail and roll the update back
    def run(*args, check=True, quiet=False):
        probe = " ".join(args)
        ok = "https://127.0.0.1:8090/healthz" in probe and "--no-check-certificate" in probe
        return subprocess.CompletedProcess(args, 0 if ok else 1, "", "")

    monkeypatch.setattr(hu, "run", run)
    monkeypatch.setattr(hu, "ready", lambda name: {"status": "ready"})
    monkeypatch.setattr(hu.time, "sleep", lambda s: None)
    assert hu.watch("app-hub-web", "app-hub-edge", 1) is True


def test_an_update_waits_for_the_old_hub_server_to_be_merged(monkeypatch, capsys):
    # the hub keeps its environment on an update: a hub still on <project>-db must move to the single Postgres first
    monkeypatch.setattr(hu, "inspect", lambda n: {"State": {"Status": "running"}, "Image": "x"})
    monkeypatch.setattr(sys, "argv", ["hub_update", "--build"])
    try:
        hu.main()
    except SystemExit as e:
        assert "scripts/db-merge.sh --project app-hub" in str(e.code)
    else:
        raise AssertionError("atualizou um hub que ainda usa app-hub-db")


def test_the_backup_dumps_the_single_server(monkeypatch, tmp_path):
    dumped: list[str] = []
    monkeypatch.setattr(hu, "inspect", lambda n: {"State": {}})
    monkeypatch.setattr(hu, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))

    def fake_run(cmd, stdout=None, check=False):
        dumped.append(cmd[2])
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(hu.subprocess, "run", fake_run)
    hu.backup("app-hub", "app-hub-web:pre-update", tmp_path)
    assert dumped == ["app-hub-app-db"]


def test_inspect_looks_only_at_containers(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker inspect <name>` searches every object type; Podman also matches a name prefix, so `app-hub-db` found
    the old `app-hub-db-data` volume and the update refused to run ("still on two servers") with no such container."""
    seen: list[tuple[str, ...]] = []

    def run(*args: str, **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return subprocess.CompletedProcess(args, 1, "", "no such container")

    monkeypatch.setattr(hu, "run", run)
    assert hu.inspect("app-hub-db") is None
    assert seen == [("docker", "inspect", "--type", "container", "app-hub-db")]


def test_rest_of_stack_never_includes_the_hub_and_keeps_running_profile_services() -> None:
    got = hu.rest_services(
        ["app-hub-web", "app-hub-edge", "app-hub-app-db"],
        ["app-hub-app-inventory", "app-hub-web"],
        "app-hub-web",
    )
    assert got == ["app-hub-app-db", "app-hub-app-inventory", "app-hub-edge"]


def test_the_roles_job_runs_before_the_apps_are_recreated() -> None:
    # the job renames the sample apps' pre-0.8 databases: an app recreated before it would find no `app_<id>`
    assert hu.jobs_first(["app-hub-app-inventory", "app-hub-db-roles", "app-hub-edge"]) == [
        "app-hub-db-roles"
    ]
    assert hu.jobs_first(["app-hub-app-inventory"]) == []


def _fake_stack(monkeypatch, argv: list[str]) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def run(*args, check=True, quiet=False, env=None):
        calls.append(args)
        out = ""
        if args[-2:] == ("config", "--services"):
            out = "app-hub-web\napp-hub-edge\n"
        elif args[:2] == ("docker", "ps"):
            out = "app-hub-web\napp-hub-app-inventory\n"
        elif args[:3] == ("docker", "image", "inspect"):
            out = "sha256:new\n"
        return subprocess.CompletedProcess(args, 0, out, "")

    main_c = {"Image": "sha256:old", "State": {"Status": "running"},
              "Config": {"Labels": {"com.docker.compose.service": "app-hub-web"}}}  # fmt: skip
    monkeypatch.setattr(hu, "run", run)
    monkeypatch.setattr(hu, "inspect", lambda n: None if n == "app-hub-db" else main_c)
    monkeypatch.setattr(hu, "compose_cmd", lambda c, *_: ["docker", "compose"])
    monkeypatch.setattr(hu, "backup", lambda *a: None)
    monkeypatch.setattr(hu, "swap", lambda *a: "x")
    monkeypatch.setattr(hu, "watch", lambda *a: True)
    monkeypatch.setattr(sys, "argv", ["hub_update", *argv, "--no-backup"])
    assert hu.main() == 0
    return calls


def test_after_a_healthy_swap_the_rest_of_the_compose_is_applied_without_the_hub(monkeypatch) -> None:
    calls = _fake_stack(monkeypatch, ["--build"])
    ups = [c for c in calls if "up" in c]
    assert ups == [("docker", "compose", "up", "-d", "--no-deps", "app-hub-app-inventory", "app-hub-edge")]
    builds = [c for c in calls if "build" in c]  # the hub, then the apps' new code before the up
    assert builds[-1] == ("docker", "compose", "build", "app-hub-app-inventory", "app-hub-edge")
    assert calls.index(builds[-1]) < calls.index(ups[0])


def test_hub_only_leaves_the_rest_of_the_stack_alone(monkeypatch) -> None:
    calls = _fake_stack(monkeypatch, ["--build", "--hub-only"])
    assert not [c for c in calls if "up" in c]


def test_builds_are_reproducible_without_the_buildx_provenance(monkeypatch) -> None:
    """buildx attaches a provenance attestation with the build time: the image id changes on every build, even fully
    cached, and compose recreates every app. Without it an unchanged app keeps its container."""
    seen: list[dict[str, str] | None] = []
    monkeypatch.setattr(hu, "run", lambda *a, check=True, quiet=False, env=None: seen.append(env))
    monkeypatch.setattr(hu, "compose_cmd", lambda c, *_: ["docker", "compose"])
    hu.build({}, "app-hub-app-inventory")
    assert seen[0] is not None and seen[0]["BUILDX_NO_DEFAULT_ATTESTATIONS"] == "1"


def test_install_builds_without_the_buildx_provenance() -> None:
    net = (Path(__file__).parents[1] / "scripts" / "install" / "net.sh").read_text(encoding="utf-8")
    body = net.split("build_env() {", 1)[1].split("\n}", 1)[0]
    assert "export BUILDX_NO_DEFAULT_ATTESTATIONS=1" in body
