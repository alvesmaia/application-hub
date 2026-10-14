"""scripts/install.sh --config-only: interactive answers (INSTALL_TTY) become .prod.env; Enter keeps the recommended."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def install(tmp_path: Path, answers: list[str]) -> tuple[dict[str, str], str]:
    pp = tmp_path / "platform-py"
    (pp / "scripts").mkdir(parents=True)
    for f in ("scripts/install.sh", "prod.env.example", "docker-compose.yml"):
        shutil.copy(ROOT / f, pp / f)
    tty = tmp_path / "answers.txt"
    tty.write_text("\n".join(answers) + "\n", encoding="utf-8", newline="\n")
    r = subprocess.run(  # noqa: S603
        [str(BASH), "scripts/install.sh", "--config-only"],
        cwd=pp,
        env={
            "PATH": str(Path(str(BASH)).parent) + ":/usr/bin:/bin",
            "INSTALL_TTY": str(tty),
            "HOME": str(tmp_path),
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    text = (pp / ".prod.env").read_text(encoding="utf-8")
    env = dict(
        line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#") and "=" in line
    )
    return env, r.stderr


BASE = ["", "", "", "", "", "", "n"]  # e-mail, port, bind, public URL, project, subnet, sample apps (no)


def test_enter_everywhere_keeps_the_recommended_options(tmp_path: Path) -> None:
    env, _ = install(tmp_path, [*BASE, "", ""])  # advanced options? Enter (= no) · confirm: Enter (= yes)
    assert env["HUB_PROJECT"] == "app-hub" and env["HUB_PORT"] == "8090"
    for k in (
        "HUB_SSO_PROVIDER",
        "HUB_TELEMETRY",
        "HUB_UPDATE_CHECK",
        "HUB_OBJSTORE_DIR",
        "HUB_BACKUP_OFFSITE_URL",
    ):
        assert k not in env, k
    assert env["HUB_PUBLIC_DOCS"] == "1"


def test_advanced_answers_with_enter_keep_the_recommended_too(tmp_path: Path) -> None:
    adv = ["s"] + [
        ""
    ] * 9  # objstore, anchor, webhook, off-site, Entra, CIDRs, proxy, telemetry, OTLP, updates…
    env, _ = install(tmp_path, [*BASE, *adv, "", "", ""])
    assert (
        "HUB_SSO_PROVIDER" not in env and "HUB_TELEMETRY" not in env and "HUB_TRUST_PROXY_HEADERS" not in env
    )
    assert env["HUB_PUBLIC_DOCS"] == "1" and "HUB_BUILD_CPUS" not in env


def test_every_advanced_answer_reaches_prod_env_and_secrets_stay_off_the_screen(tmp_path: Path) -> None:
    adv = [
        "s",
        "/srv/minio",  # MinIO data folder
        "/mnt/nfs/ancora",  # audit anchor folder
        "https://ancora.exemplo.com/head",  # anchor webhook
        "tok-ancora-123",  # webhook token (no echo)
        "s3",  # off-site copy
        "https://k:segredo-s3@s3.exemplo.com:9000/bk/hub",  # S3 URL (no echo)
        "s",  # Entra ID
        "7b369f6a-86a3-4b85-9b78-ac3bdf47a74d",
        "daba04dc-e352-4c55-8815-a19bd702b569",
        "exemplo.com",
        "Leitor",
        "",  # local login: admins (recommended)
        "segredo-entra-456",  # client secret (no echo; goes to the vault, not to .prod.env)
        "10.0.0.0/8",  # admin CIDRs
        "s",  # proxy in front
        "",  # trusted proxies: recommended
        "n",  # telemetry off
        "n",  # update notice off
        "n",  # public docs off
        "4",  # build CPUs
        "",  # confirm
    ]
    env, err = install(tmp_path, [*BASE, *adv])
    assert env["HUB_OBJSTORE_DIR"] == "/srv/minio" and env["HUB_AUDIT_HEAD_DIR"] == "/mnt/nfs/ancora"
    assert (
        env["HUB_AUDIT_HEAD_URL"] == "https://ancora.exemplo.com/head"
        and env["HUB_AUDIT_HEAD_TOKEN"] == "tok-ancora-123"
    )
    assert env["HUB_BACKUP_OFFSITE_URL"] == "https://k:segredo-s3@s3.exemplo.com:9000/bk/hub"
    assert env["HUB_SSO_PROVIDER"] == "entra" and env["HUB_ENTRA_CLIENT_ID"].startswith("daba04dc")
    assert env["HUB_ENTRA_ALLOWED_DOMAINS"] == "exemplo.com" and env["HUB_ENTRA_DEFAULT_ROLE"] == "Leitor"
    assert env["HUB_LOCAL_LOGIN"] == "admins" and env["HUB_ADMIN_ALLOWED_CIDRS"] == "10.0.0.0/8"
    assert env["HUB_TRUST_PROXY_HEADERS"] == "1" and env["HUB_TRUSTED_PROXIES"] == "172.16.0.0/12"
    assert env["HUB_TELEMETRY"] == "0" and env["HUB_UPDATE_CHECK"] == "0" and env["HUB_PUBLIC_DOCS"] == "0"
    assert env["HUB_BUILD_CPUS"] == "4"
    text = (tmp_path / "platform-py" / ".prod.env").read_text(encoding="utf-8")
    assert "segredo-entra-456" not in text  # Entra secret goes to the vault in the install step
    for secret in ("segredo-entra-456", "segredo-s3", "tok-ancora-123"):
        assert secret not in err  # never shown back
