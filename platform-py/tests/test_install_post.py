"""install.sh after the start: the sample bucket, checks from inside the hub, the final message and the saved command."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from install_helper import BASH, ROOT, run_install, sh_path

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def post(
    tmp_path: Path, script: str, docker: str = "exit 0", env: dict[str, str] | None = None
) -> tuple[int, str]:
    """Sources scripts/install/post.sh (and net.sh for mask_url) with a fake docker and runs `script`."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir(exist_ok=True)
    (fakebin / "docker").write_text(f"#!/bin/sh\n{docker}\n", encoding="utf-8", newline="\n")
    (fakebin / "docker").chmod(0o755)
    (fakebin / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8", newline="\n")
    (fakebin / "sleep").chmod(0o755)
    lib = sh_path(ROOT / "scripts" / "install")
    r = subprocess.run(  # noqa: S603
        [str(BASH), "-c", f"set -euo pipefail; exec 4>&2; . {lib}/net.sh; . {lib}/post.sh; {script}"],
        cwd=tmp_path,
        env={
            "PATH": f"{sh_path(fakebin)}:{Path(str(BASH)).parent}:/usr/bin:/bin",
            "HOME": str(tmp_path),
            **(env or {}),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    return r.returncode, r.stdout + r.stderr


def test_a_bucket_that_keeps_failing_shows_the_error_and_the_likely_cause(tmp_path: Path) -> None:
    docker = 'echo "mc: <ERROR> Unable to make bucket r/fornecedores. Access Denied." >&2; exit 1'
    rc, out = post(tmp_path, "P=app-hub HUB_MC_IMAGE=mc APP_HUB_OBJSTORE_PASSWORD=x make_bucket", docker)
    assert rc == 0  # the hub works without the sample bucket: warn, do not abort
    assert (
        "Access Denied" in out and "proxy" in out and out.count("Unable to make bucket") == 1
    )  # once, not 60x


def test_the_hub_reaching_minio_and_the_apps_is_checked_from_inside(tmp_path: Path) -> None:
    rc, out = post(tmp_path, "P=app-hub final_checks", 'case "$*" in *objstore*) exit 1 ;; *) exit 0 ;; esac')
    assert rc == 0 and "MinIO" in out and "✘" in out and "--no-proxy" in out
    rc, out = post(tmp_path, "P=app-hub final_checks")
    assert "✔" in out and "✘" not in out


def test_the_final_message_gives_absolute_paths(tmp_path: Path) -> None:
    _rc, out = post(tmp_path, "P=app-hub HUB_PORT=8090 HUB_PUBLIC_URL=http://h:8090 final_message")
    # absolute: it is read from any folder (the old message was relative to the repository root)
    assert re.search(r"cat /\S+/\.secrets/primeiro-acesso\.txt", out), out
    assert re.search(r" /\S+/\.secrets/master\.key", out), out


def test_the_command_is_saved_without_secrets_for_the_next_server(tmp_path: Path) -> None:
    script = (
        "EMAIL=ops@exemplo.com PORT=8095 BIND=0.0.0.0 PUBLIC_URL=https://hub.empresa.local PROJECT=app-hub SUBNET=10.231"
        " DEMO=0 NET_PROXY=http://svc:s3nh4@proxy.empresa.local:8080 NET_NOPROXY=.empresa.local CA_FILE=/etc/ca.pem"
        " PYPI= REG_MIRROR= APK= GOPX= DATA_DIR=/data/app-hub ENGINE_STORE= TLS_CERT=/etc/hub.crt TLS_KEY=/etc/hub.key"
        " save_install_cmd; cat .secrets/install-cmd.txt"
    )
    rc, out = post(tmp_path, script)
    assert rc == 0, out
    for part in (
        "--admin-email ops@exemplo.com",
        "--port 8095",
        "--no-demo-apps",
        "--proxy 'http://***@proxy.empresa.local:8080'",
        "--ca-file /etc/ca.pem",
        "--data-dir /data/app-hub",
        "--tls-key /etc/hub.key",
    ):
        assert part in out, part
    assert "s3nh4" not in out


def test_a_rerun_with_prod_env_asks_nothing_again(tmp_path: Path) -> None:
    (tmp_path / "platform-py").mkdir()
    (tmp_path / "platform-py" / ".prod.env").write_text(
        "HUB_PROJECT=app-hub\nHUB_PORT=8090\n", encoding="utf-8"
    )
    r = run_install(tmp_path, (), answers=["n"])  # the only answer: the consent (no)
    assert "E-mail do primeiro administrador" not in r.stderr and "usando platform-py/.prod.env" in r.stderr
    assert "cancelado" in r.stderr
