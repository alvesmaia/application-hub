"""install.sh --tls-cert/--tls-key: the edge serves HTTPS with the company certificate (needed for Entra sign-in)."""

from __future__ import annotations

from pathlib import Path

import pytest
from install_helper import BASH, ROOT, prod_env, run_install, sh_path

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

CERT = "-----BEGIN CERTIFICATE-----\nMIIBcert\n-----END CERTIFICATE-----\n"
KEY = "-----BEGIN PRIVATE KEY-----\nMIIEkey-segredo-tls\n-----END PRIVATE KEY-----\n"


def files(tmp_path: Path, cert: str = CERT, key: str = KEY) -> tuple[str, str]:
    (tmp_path / "hub.crt").write_text(cert, encoding="utf-8")
    (tmp_path / "hub.key").write_text(key, encoding="utf-8")
    return sh_path(tmp_path / "hub.crt"), sh_path(tmp_path / "hub.key")


def test_certificate_and_key_turn_on_https_at_the_edge(tmp_path: Path) -> None:
    cert, key = files(tmp_path)
    r = run_install(tmp_path, ("--config-only", "--yes", "--tls-cert", cert, "--tls-key", key))
    assert r.returncode == 0, r.stderr
    env = prod_env(tmp_path)
    assert env["HUB_TLS_DIR"] == "./.secrets/tls" and env["HUB_EDGE_TLS"] == "tls.caddy"
    assert env["HUB_PUBLIC_URL"] == "https://localhost:8090" and env["HUB_COOKIE_SECURE"] == "1"
    tls = tmp_path / "platform-py" / ".secrets" / "tls"
    assert (tls / "cert.pem").read_text(encoding="utf-8") == CERT
    assert (tls / "key.pem").read_text(encoding="utf-8") == KEY
    assert "sem HTTPS" not in r.stderr and "segredo-tls" not in r.stderr + r.stdout


def test_a_public_url_given_with_tls_is_kept(tmp_path: Path) -> None:
    cert, key = files(tmp_path)
    args = (
        "--config-only",
        "--yes",
        "--tls-cert",
        cert,
        "--tls-key",
        key,
        "--public-url",
        "https://hub.empresa.local:9443",
    )
    r = run_install(tmp_path, args)
    assert r.returncode == 0 and prod_env(tmp_path)["HUB_PUBLIC_URL"] == "https://hub.empresa.local:9443"


def test_certificate_without_key_or_wrong_files_are_refused(tmp_path: Path) -> None:
    cert, key = files(tmp_path)
    r = run_install(tmp_path / "a", ("--config-only", "--yes", "--tls-cert", cert))
    assert r.returncode == 2 and "--tls-key" in r.stderr
    r = run_install(tmp_path / "b", ("--config-only", "--yes", "--tls-cert", key, "--tls-key", key))
    assert r.returncode == 2 and "BEGIN CERTIFICATE" in r.stderr and "segredo-tls" not in r.stderr
    r = run_install(tmp_path / "c", ("--config-only", "--yes", "--tls-cert", cert, "--tls-key", cert))
    assert r.returncode == 2 and "PRIVATE KEY" in r.stderr


def test_interactive_question_after_the_url(tmp_path: Path) -> None:
    cert, key = files(tmp_path)
    answers = ["", "", "", "https://hub.empresa.local:8090", cert, key, "", "", "n", "n", "", ""]
    r = run_install(tmp_path, ("--config-only",), answers=answers)
    assert r.returncode == 0, r.stderr
    env = prod_env(tmp_path)
    assert env["HUB_EDGE_TLS"] == "tls.caddy" and env["HUB_PUBLIC_URL"] == "https://hub.empresa.local:8090"


def test_edge_config_imports_tls_only_when_asked() -> None:
    caddy = (ROOT / "infra" / "edge" / "Caddyfile").read_text(encoding="utf-8")
    assert "import {$HUB_EDGE_TLS:plain.caddy}" in caddy and "auto_https disable_redirects" in caddy
    assert "tls /certs/cert.pem /certs/key.pem" in (ROOT / "infra" / "edge" / "tls.caddy").read_text(
        encoding="utf-8"
    )
    assert "tls " not in (ROOT / "infra" / "edge" / "plain.caddy").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./infra/edge:/etc/caddy:ro" in compose and "${HUB_TLS_DIR:-./infra/edge}:/certs:ro" in compose
    assert "HUB_EDGE_TLS: ${HUB_EDGE_TLS:-plain.caddy}" in compose
