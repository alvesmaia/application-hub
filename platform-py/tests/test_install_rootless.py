"""install.sh with rootless Podman: with or without a systemd user session, the socket and the start after a reboot."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from install_helper import BASH, ROOT, prod_env, run_install, sh_path

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

# docker = podman-docker; the first real engine call (image inspect) fails, so the run stops before building
FAKE_DOCKER = r"""
case "$1 ${2:-}" in
  "compose version") exit 0 ;;
  "--version "*) echo "podman version 5.8.2"; exit 0 ;;
esac
echo "DOCKER $* DH=${DOCKER_HOST:-}" >> "$FAKE_LOG"
exit 1
"""
# rootless podman whose API socket answers once `system service` (or the systemd unit) started it
FAKE_PODMAN = r"""
case "$*" in
  *"{{.Host.Security.Rootless}}"*) echo true ;;
  "--url "*" info") [ -f "$FAKE_DIR/sock-up" ] || exit 1 ;;
  *"system service"*)
    echo "PODMAN $* HC=${DISABLE_HC_SYSTEMD:-} STORE=${CONTAINERS_STORAGE_CONF:-} XDG=${XDG_RUNTIME_DIR:-} PX=${HTTPS_PROXY:-} NP=${NO_PROXY:-} CERT=${SSL_CERT_FILE:-}" >> "$FAKE_LOG"
    touch "$FAKE_DIR/sock-up" ;;
esac
exit 0
"""
FAKE_CRONTAB = r"""
if [ "$1" = -l ]; then cat "$FAKE_DIR/crontab" 2>/dev/null || exit 1; else cat > "$FAKE_DIR/crontab"; fi
"""
FAKE_SYSTEMCTL = r"""
echo "SYSTEMCTL $*" >> "$FAKE_LOG"
case "$*" in *"podman.socket"*) touch "$FAKE_DIR/sock-up" ;; esac
"""
FAKES = {"docker": FAKE_DOCKER, "podman": FAKE_PODMAN, "crontab": FAKE_CRONTAB, "systemctl": FAKE_SYSTEMCTL}
FLAGS = ("--yes", "--skip-doctor", "--admin-email", "ops@exemplo.com")


def rootless(tmp_path: Path, *args: str, systemd: bool, bus: bool) -> tuple[int, str, str]:
    xdg, sysd, fake = tmp_path / "xdg", tmp_path / "run-systemd-system", tmp_path / "fake"
    xdg.mkdir(exist_ok=True)
    fake.mkdir(exist_ok=True)
    if systemd:
        sysd.mkdir(exist_ok=True)
    if bus:
        (xdg / "bus").touch()
    log = fake / "log"
    log.touch()
    r = run_install(
        tmp_path,
        (*FLAGS, *args),
        fakes=FAKES,
        env_extra={
            "XDG_RUNTIME_DIR": str(xdg),
            "INSTALL_SYSTEMD_DIR": str(sysd),
            "FAKE_DIR": str(fake),
            "FAKE_LOG": str(log),
        },
    )
    return r.returncode, r.stdout + r.stderr, log.read_text(encoding="utf-8")


def crontab(tmp_path: Path) -> str:
    f = tmp_path / "fake" / "crontab"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def test_without_systemd_the_hub_runs_its_own_socket_and_starts_from_cron(tmp_path: Path) -> None:
    rc, out, log = rootless(tmp_path, systemd=False, bus=False)
    sock = f"{tmp_path / 'xdg'}/podman/podman.sock"
    assert "--cgroup-manager=cgroupfs" in log and "HC=true" in log and f"unix://{sock}" in log
    assert f"DH=unix://{sock}" in log  # the install's compose/docker calls use that socket
    env = prod_env(tmp_path)
    assert env["HUB_CONTAINER_SOCKET_HOST"] == sock and env["HUB_NO_SYSTEMD"] == "1"
    assert "@reboot" in crontab(tmp_path) and "scripts/hub-start.sh" in crontab(tmp_path)
    assert "crontab" in out  # the consent said so
    assert rc == 1  # stopped at the fake engine, after the setup


def test_cron_line_is_added_only_once(tmp_path: Path) -> None:
    rootless(tmp_path, systemd=False, bus=False)
    rootless(tmp_path, systemd=False, bus=False)  # running the install again (same folder)
    assert crontab(tmp_path).count("@reboot") == 1


def test_systemd_without_the_user_session_stops_early_with_the_admin_commands(tmp_path: Path) -> None:
    rc, out, _ = rootless(tmp_path, systemd=True, bus=False)
    assert rc == 1 and "loginctl enable-linger" in out and "systemctl restart user@" in out
    assert not (tmp_path / "platform-py" / ".prod.env").exists()  # nothing written, nothing started


def test_with_the_user_session_systemd_starts_the_socket_and_the_containers_after_a_reboot(
    tmp_path: Path,
) -> None:
    _rc, out, log = rootless(tmp_path, systemd=True, bus=True)
    assert "SYSTEMCTL --user enable --now podman.socket podman-restart.service" in log
    assert "system service" not in log and crontab(tmp_path) == ""
    assert "HUB_NO_SYSTEMD" not in prod_env(tmp_path) and "podman-restart" in out


def test_autostart_none_changes_nothing_in_the_user_session(tmp_path: Path) -> None:
    _rc, _out, log = rootless(tmp_path, "--autostart", "none", systemd=False, bus=False)
    assert crontab(tmp_path) == "" and "system service" in log  # the socket still runs, no start at boot


def test_no_systemd_flag_forces_the_mode_on_a_systemd_host(tmp_path: Path) -> None:
    _rc, _out, log = rootless(tmp_path, "--no-systemd", systemd=True, bus=False)
    assert "--cgroup-manager=cgroupfs" in log and "SYSTEMCTL" not in log


def test_hub_start_script_brings_up_the_socket_and_the_stack(tmp_path: Path) -> None:
    rootless(tmp_path, systemd=False, bus=False)
    pp = tmp_path / "platform-py"
    (pp / "scripts" / "hub-start.sh").write_bytes((ROOT / "scripts" / "hub-start.sh").read_bytes())
    (tmp_path / "fake" / "sock-up").unlink()  # after the reboot: no socket
    log = tmp_path / "fake" / "log"
    env = {
        "PATH": f"{tmp_path / 'fakebin'}:{Path(str(BASH)).parent}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "FAKE_DIR": str(tmp_path / "fake"),
        "FAKE_LOG": str(log),
    }
    subprocess.run([str(BASH), "scripts/hub-start.sh"], cwd=pp, env=env, check=False, capture_output=True)  # noqa: S603
    text = log.read_text(encoding="utf-8")
    assert text.count("system service") == 2 and "DOCKER compose" in text and " up -d" in text


def test_the_cron_line_points_to_the_install_folder_even_when_run_from_elsewhere(tmp_path: Path) -> None:
    rootless(tmp_path, systemd=False, bus=False)
    line = crontab(tmp_path)  # absolute paths of this platform-py (Git Bash may show the temp folder as /tmp)
    assert line.startswith("@reboot bash /") and "/platform-py/scripts/hub-start.sh >> /" in line
    assert line.rstrip().endswith("/platform-py/.secrets/hub-start.log 2>&1")


def test_engine_store_keeps_the_images_in_the_hub_own_store(tmp_path: Path) -> None:
    store = tmp_path / "imagens"
    _rc, out, log = rootless(tmp_path, "--engine-store", sh_path(store), systemd=True, bus=True)
    conf = store / "storage.conf"
    assert f'graphroot = "{sh_path(store)}/storage"' in conf.read_text(encoding="utf-8")
    assert f"STORE={sh_path(conf)}" in log and f"XDG={sh_path(store)}/run" in log
    assert f"unix://{sh_path(store)}/run/podman/podman.sock" in log and "SYSTEMCTL" not in log
    env = prod_env(tmp_path)
    assert env["HUB_ENGINE_STORE"] == sh_path(store)
    assert env["HUB_CONTAINER_SOCKET_HOST"] == f"{sh_path(store)}/run/podman/podman.sock"
    hub_env = (tmp_path / "platform-py" / ".secrets" / "hub-env.sh").read_text(encoding="utf-8")
    assert f"CONTAINERS_STORAGE_CONF={sh_path(conf)}" in hub_env and "DOCKER_HOST=unix://" in hub_env
    assert "source" in out and "hub-env.sh" in out


def test_own_socket_mode_also_writes_hub_env(tmp_path: Path) -> None:
    rootless(tmp_path, systemd=False, bus=False)
    hub_env = (tmp_path / "platform-py" / ".secrets" / "hub-env.sh").read_text(encoding="utf-8")
    assert "DOCKER_HOST=unix://" in hub_env and "DISABLE_HC_SYSTEMD=true" in hub_env
    assert "CONTAINERS_STORAGE_CONF" not in hub_env


PEM = "-----BEGIN CERTIFICATE-----\nMIIBfakeCA\n-----END CERTIFICATE-----\n"


def test_the_own_socket_pulls_images_through_the_proxy_trusting_the_ca(tmp_path: Path) -> None:
    (tmp_path / "ca.pem").write_text(PEM, encoding="utf-8")
    flags = ("--proxy", "http://proxy.empresa.local:8080", "--ca-file", sh_path(tmp_path / "ca.pem"))
    _rc, _out, log = rootless(tmp_path, *flags, systemd=False, bus=False)
    line = next(ln for ln in log.splitlines() if "system service" in ln)
    assert "PX=http://proxy.empresa.local:8080" in line and "NP=localhost,127.0.0.1" in line
    bundle = line.split("CERT=", 1)[1].strip()
    written = (tmp_path / "platform-py" / ".secrets" / "ca" / "bundle.pem").read_text(encoding="utf-8")
    assert bundle.endswith("/.secrets/ca/bundle.pem") and PEM in written


def test_with_systemd_the_podman_service_gets_the_proxy_and_the_ca_in_a_drop_in(tmp_path: Path) -> None:
    (tmp_path / "ca.pem").write_text(PEM, encoding="utf-8")
    flags = ("--proxy", "http://proxy.empresa.local:8080", "--ca-file", sh_path(tmp_path / "ca.pem"))
    _rc, out, log = rootless(tmp_path, *flags, systemd=True, bus=True)
    drop = tmp_path / ".config" / "systemd" / "user" / "podman.service.d" / "app-hub-network.conf"
    text = drop.read_text(encoding="utf-8")
    assert 'Environment="HTTPS_PROXY=http://proxy.empresa.local:8080"' in text and "SSL_CERT_FILE=" in text
    assert "SYSTEMCTL --user daemon-reload" in log and "podman.service.d" in out  # listed in the consent
