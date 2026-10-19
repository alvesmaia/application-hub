"""scripts/install.sh --config-only: interactive answers (INSTALL_TTY) become .prod.env; Enter keeps the recommended."""

from __future__ import annotations

from pathlib import Path

import pytest
from install_helper import BASH, ROOT, prod_env, run_install

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def install(tmp_path: Path, answers: list[str], args: tuple[str, ...] = ()) -> tuple[dict[str, str], str]:
    r = run_install(tmp_path, ("--config-only", *args), answers)
    assert r.returncode == 0, r.stderr[-2000:]
    return prod_env(tmp_path), r.stderr


BASE = [
    "",
    "",
    "",
    "",
    "",
    "",
    "n",
    "",
]  # e-mail, port, bind, URL, project, subnet, sample apps (no), network (no)


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
        "11111111-2222-4333-8444-555555555555",
        "66666666-7777-4888-9999-000000000000",
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
    assert env["HUB_SSO_PROVIDER"] == "entra" and env["HUB_ENTRA_CLIENT_ID"].startswith("66666666")
    assert env["HUB_ENTRA_ALLOWED_DOMAINS"] == "exemplo.com" and env["HUB_ENTRA_DEFAULT_ROLE"] == "Leitor"
    assert env["HUB_LOCAL_LOGIN"] == "admins" and env["HUB_ADMIN_ALLOWED_CIDRS"] == "10.0.0.0/8"
    assert env["HUB_TRUST_PROXY_HEADERS"] == "1" and env["HUB_TRUSTED_PROXIES"] == "172.16.0.0/12"
    assert env["HUB_TELEMETRY"] == "0" and env["HUB_UPDATE_CHECK"] == "0" and env["HUB_PUBLIC_DOCS"] == "0"
    assert env["HUB_BUILD_CPUS"] == "4"
    text = (tmp_path / "platform-py" / ".prod.env").read_text(encoding="utf-8")
    assert "segredo-entra-456" not in text  # Entra secret goes to the vault in the install step
    for secret in ("segredo-entra-456", "segredo-s3", "tok-ancora-123"):
        assert secret not in err  # never shown back


def test_offsite_url_is_written_without_sed_so_any_folder_name_is_safe() -> None:
    # a '#' or '&' in SP_FOLDER broke `sed s#...#$OFF#`; setv writes the value verbatim
    src = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    assert "sed -i" not in src
    assert 'setv HUB_BACKUP_OFFSITE_URL "sharepoint://backups-sharepoint/' in src


def test_http_public_url_relaxes_the_secure_cookie_and_warns(tmp_path: Path) -> None:
    # a fixed Secure cookie over http:// is dropped by the browser: the login loops back to /login
    env, err = install(tmp_path, [], ("--yes", "--public-url", "http://hub.interno:8090"))
    assert env["HUB_COOKIE_SECURE"] == "auto"
    assert "HTTPS" in err and "http://hub.interno:8090" in err


def test_https_and_localhost_urls_need_no_warning(tmp_path: Path) -> None:
    env, err = install(tmp_path / "a", [], ("--yes", "--public-url", "https://hub.exemplo.com"))
    assert env["HUB_COOKIE_SECURE"] == "1" and "HTTPS" not in err
    env, err = install(tmp_path / "b", [], ("--yes",))  # http://localhost: browsers keep Secure cookies there
    assert env["HUB_COOKIE_SECURE"] == "auto" and "HTTPS" not in err


def test_prod_compose_takes_the_cookie_setting_from_prod_env() -> None:
    src = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "HUB_COOKIE_SECURE: ${HUB_COOKIE_SECURE:-1}" in src
