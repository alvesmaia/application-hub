"""install.sh without questions: sign-in with Microsoft (Entra) and a reverse proxy in front, from flags."""

from __future__ import annotations

from pathlib import Path

import pytest
from install_helper import BASH, prod_env, run_install
from test_install_post import post

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "66666666-7777-8888-9999-000000000000"


def secret_file(tmp_path: Path) -> str:
    f = tmp_path / "entra-secret.txt"
    f.write_bytes(b"s3gr3d0-entra\r\n")  # a Windows copy ends in CRLF
    return str(f)


def entra(tmp_path: Path, *extra: str) -> tuple[str, ...]:
    return (
        "--entra-tenant", TENANT, "--entra-client-id", CLIENT,
        "--entra-client-secret-file", secret_file(tmp_path), *extra,
    )  # fmt: skip


def test_entra_flags_reach_prod_env_and_the_secret_never_does(tmp_path: Path) -> None:
    args = entra(
        tmp_path, "--entra-domains", "empresa.com", "--entra-role", "Power User", "--local-login", "nenhum"
    )
    r = run_install(tmp_path, ("--config-only", "--yes", *args))
    assert r.returncode == 0, r.stderr
    env = prod_env(tmp_path)
    assert env["HUB_SSO_PROVIDER"] == "entra" and env["HUB_ENTRA_TENANT_ID"] == TENANT
    assert env["HUB_ENTRA_CLIENT_ID"] == CLIENT and env["HUB_ENTRA_ALLOWED_DOMAINS"] == "empresa.com"
    # a space in the value: quoted, so `. ./.prod.env` (hub-start.sh, --doctor) and compose read it whole
    assert env["HUB_ENTRA_DEFAULT_ROLE"] == "'Power User'" and env["HUB_LOCAL_LOGIN"] == "0"
    text = (tmp_path / "platform-py" / ".prod.env").read_text(encoding="utf-8")
    assert "s3gr3d0" not in text and "s3gr3d0" not in r.stdout + r.stderr
    assert "cofre" in r.stderr  # --config-only does not store the secret: it says so


def test_entra_defaults_keep_password_login_for_administrators(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes", *entra(tmp_path)))
    env = prod_env(tmp_path)
    assert (
        r.returncode == 0
        and env["HUB_LOCAL_LOGIN"] == "admins"
        and env["HUB_ENTRA_DEFAULT_ROLE"] == "User"  # the default roles: Administrador, Power User, User
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--entra-tenant", TENANT), "--entra-client-id"),  # incomplete: the three are needed
        (("--entra-tenant", "nao-e-guid", "--entra-client-id", CLIENT), "--entra-tenant"),
        (("--local-login", "nenhum"), "--local-login"),  # without Entra nobody could sign in
        (("--trusted-proxy", "proxy.empresa.local"), "--trusted-proxy"),
    ],
)
def test_wrong_or_incomplete_flags_are_refused(tmp_path: Path, args: tuple[str, ...], expected: str) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes", *args))
    assert r.returncode == 2 and expected in r.stderr, r.stderr
    assert not (tmp_path / "platform-py" / ".prod.env").exists()


def test_a_missing_secret_file_is_refused(tmp_path: Path) -> None:
    args = (
        "--entra-tenant",
        TENANT,
        "--entra-client-id",
        CLIENT,
        "--entra-client-secret-file",
        "/nao/existe",
    )
    r = run_install(tmp_path, ("--config-only", "--yes", *args))
    assert r.returncode == 2 and "/nao/existe" in r.stderr


def test_the_secret_is_read_from_the_file_without_the_line_break(tmp_path: Path) -> None:
    script = (
        f"yes_no() {{ case \"$1\" in s) return 0 ;; *) return 1 ;; esac; }}; ENTRA=s ENTRA_TENANT={TENANT} ENTRA_CLIENT={CLIENT} ENTRA_SECRET=''"
        f" ENTRA_SECRET_FILE={secret_file(tmp_path).replace(chr(92), '/')} ENTRA_DOMAINS='' ENTRA_ROLE=Colaborador"
        " ENTRA_LOCAL=admins PROXY='' PROXIES='' CONFIG_ONLY=0; opts_validate; printf '[%s]' \"$ENTRA_SECRET\""
    )
    rc, out = post(tmp_path, f". {Path(__file__).parents[1].as_posix()}/scripts/install/opts.sh; {script}")
    assert rc == 0 and "[s3gr3d0-entra]" in out, out


def test_trusted_proxy_flag_trusts_the_forwarded_headers(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes", "--trusted-proxy", "127.0.0.1/32,10.0.0.0/8"))
    env = prod_env(tmp_path)
    assert r.returncode == 0 and env["HUB_TRUST_PROXY_HEADERS"] == "1"
    assert env["HUB_TRUSTED_PROXIES"] == "127.0.0.1/32,10.0.0.0/8"


def test_the_final_message_gives_the_redirect_uri_to_register(tmp_path: Path) -> None:
    script = "P=app-hub HUB_PORT=8090 HUB_PUBLIC_URL=https://hub.empresa.local HUB_SSO_PROVIDER=entra final_message"
    _rc, out = post(tmp_path, script)
    assert "https://hub.empresa.local/auth/entra/callback" in out


def test_the_saved_command_keeps_the_entra_and_proxy_flags_but_not_the_secret(tmp_path: Path) -> None:
    script = (
        "EMAIL=a@b.c PORT=8090 BIND=127.0.0.1 PUBLIC_URL= PROJECT=app-hub SUBNET=10.231 DEMO=1 NET_PROXY= NET_NOPROXY="
        " CA_FILE= PYPI= REG_MIRROR= APK= GOPX= DATA_DIR= ENGINE_STORE= TLS_CERT= TLS_KEY="
        f" ENTRA=s ENTRA_TENANT={TENANT} ENTRA_CLIENT={CLIENT} ENTRA_SECRET=s3gr3d0 ENTRA_SECRET_FILE=/etc/hub/entra.txt"
        " ENTRA_DOMAINS= ENTRA_ROLE=Colaborador ENTRA_LOCAL=admins PROXY=s PROXIES=127.0.0.1/32"
        " save_install_cmd; cat .secrets/install-cmd.txt"
    )
    rc, out = post(tmp_path, script)
    assert rc == 0, out
    assert f"--entra-tenant {TENANT}" in out and "--entra-client-secret-file /etc/hub/entra.txt" in out
    assert "--trusted-proxy 127.0.0.1/32" in out and "s3gr3d0" not in out
    assert "--entra-role 'Colaborador'" in out  # quoted: "Power User" has a space
