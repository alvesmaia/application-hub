"""hub_update.py: the running image gets a tag before the build (in containerd, without a tag it vanishes from the ID)."""

import importlib.util
import subprocess
import sys
from pathlib import Path

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
    monkeypatch.setattr(hu, "inspect", lambda n: main_c)
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
