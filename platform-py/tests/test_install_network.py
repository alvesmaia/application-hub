"""install.sh on a corporate network: proxy, own CA and internal mirrors reach .prod.env and every build."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from install_helper import BASH, RECORDING_DOCKER, ROOT, prod_env, run_install

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

PEM = "-----BEGIN CERTIFICATE-----\nMIIBfakeCA\n-----END CERTIFICATE-----\n"
PROXY = "http://proxy.empresa.local:8080"
PYPI = "https://repo.empresa.local/api/pypi/pypi/simple"
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.apps.yml")


def net_flags(tmp_path: Path, proxy: str = PROXY) -> tuple[str, ...]:
    ca = tmp_path / "ca-empresa.pem"
    ca.write_text(PEM, encoding="utf-8")
    return (
        "--admin-email", "ops@exemplo.com",
        "--proxy", proxy, "--no-proxy", ".empresa.local,10.0.0.0/8", "--ca-file", str(ca),
        "--pypi-index", PYPI, "--registry-mirror", "docker.repo.empresa.local",
        "--apk-mirror", "https://repo.empresa.local/alpine", "--goproxy", "https://repo.empresa.local/go",
    )  # fmt: skip


def test_network_options_reach_prod_env_and_the_ca_is_copied(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", *net_flags(tmp_path)))
    assert r.returncode == 0, r.stderr
    env = prod_env(tmp_path)
    assert env["HUB_PROXY"] == PROXY and env["HUB_NO_PROXY"] == ".empresa.local,10.0.0.0/8"
    assert env["HUB_PYPI_INDEX"] == PYPI and env["HUB_IMAGE_PREFIX"] == "docker.repo.empresa.local/library/"
    assert env["HUB_APK_MIRROR"] == "https://repo.empresa.local/alpine"
    assert env["HUB_GOPROXY"] == "https://repo.empresa.local/go"
    assert env["HUB_CA_FILE"] == "./.secrets/ca/ca.pem" and env["HUB_CA_DIR"] == "./.secrets/ca"
    assert (tmp_path / "platform-py" / ".secrets" / "ca" / "ca.pem").read_text(encoding="utf-8") == PEM


def test_without_network_options_nothing_is_written(tmp_path: Path) -> None:
    r = run_install(tmp_path, ("--config-only", "--yes"))
    assert r.returncode == 0
    env = prod_env(tmp_path)
    for k in (
        "HUB_PROXY",
        "HUB_NO_PROXY",
        "HUB_CA_FILE",
        "HUB_PYPI_INDEX",
        "HUB_IMAGE_PREFIX",
        "HUB_GOPROXY",
    ):
        assert k not in env, k


def test_a_file_that_is_not_a_pem_certificate_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "chave.txt"
    bad.write_text("not a certificate", encoding="utf-8")
    r = run_install(tmp_path, ("--config-only", "--yes", "--ca-file", str(bad)))
    assert r.returncode == 2 and "BEGIN CERTIFICATE" in r.stderr and str(bad) in r.stderr
    r = run_install(tmp_path / "m", ("--config-only", "--yes", "--ca-file", str(tmp_path / "nao-existe.pem")))
    assert r.returncode == 2 and "nao-existe.pem" in r.stderr


def test_a_proxy_password_is_never_shown(tmp_path: Path) -> None:
    flags = net_flags(tmp_path, "http://svc:s3nh4-proxy@proxy.empresa.local:8080")
    r = run_install(tmp_path, flags, answers=["n"])  # consent screen shows the proxy, then cancel
    assert "proxy.empresa.local" in r.stderr
    assert "s3nh4-proxy" not in r.stderr + r.stdout


def test_interactive_network_questions_start_from_the_proxy_already_in_the_environment(
    tmp_path: Path,
) -> None:
    base = ["", "", "", "", "", "", "n"]  # instance questions, sample apps: no
    net = ["s", "", "", "", PYPI, "", "", ""]  # network? yes · proxy and no_proxy: Enter (= from the env) · …
    r = run_install(
        tmp_path,
        ("--config-only",),
        answers=[*base, *net, "", ""],  # advanced: no · write: yes
        env_extra={"HTTPS_PROXY": PROXY, "NO_PROXY": "localhost,.empresa.local"},
    )
    assert r.returncode == 0, r.stderr
    env = prod_env(tmp_path)
    assert env["HUB_PROXY"] == PROXY and env["HUB_NO_PROXY"] == "localhost,.empresa.local"
    assert env["HUB_PYPI_INDEX"] == PYPI and "HUB_CA_FILE" not in env


def test_every_build_gets_the_proxy_ca_and_mirrors(tmp_path: Path) -> None:
    log = tmp_path / "builds.log"
    r = run_install(
        tmp_path,
        ("--yes", "--skip-doctor", *net_flags(tmp_path)),  # the diagnosis has its own tests
        fakes={"docker": RECORDING_DOCKER},
        env_extra={"FAKE_LOG": str(log)},
    )
    text = log.read_text(encoding="utf-8")
    mc, compose = text.split("ARGS compose", 1)
    for part in (mc, compose):
        assert (
            f"HTTPS_PROXY={PROXY}" in part
            and "NO_PROXY=localhost,127.0.0.1,.empresa.local,10.0.0.0/8" in part
        )
        assert "EXTRA_CA_PEM=-----BEGIN CERTIFICATE-----" in part
        assert "IMAGE_PREFIX=docker.repo.empresa.local/library/" in part
    mc_args = mc.split("\n", 1)[0] + " "
    for arg in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "EXTRA_CA_PEM",
        "IMAGE_PREFIX",
        "APK_MIRROR",
        "GOPROXY",
    ):
        assert f"--build-arg {arg} " in mc_args, (
            arg
        )  # docker build of mc: passed by name, the value stays in the env
    assert f"PIP_INDEX_URL={PYPI}" in compose and f"UV_DEFAULT_INDEX={PYPI}" in compose
    assert "APK_MIRROR=https://repo.empresa.local/alpine" in mc and r.returncode != 0


DOCKERFILES = [
    "platform-py/Dockerfile",
    "platform-py/infra/minio/Dockerfile",
    "platform/demo-apps-py/ativos/Dockerfile",
    "platform/demo-apps-py/fornecedores/Dockerfile",
    "platform/demo-apps-py/servicos/Dockerfile",
    "platform/Dockerfile.coletor",
]


@pytest.mark.parametrize("rel", DOCKERFILES)
def test_dockerfiles_take_the_image_prefix_and_the_extra_ca(rel: str) -> None:
    src = (ROOT.parent / rel).read_text(encoding="utf-8")
    froms = [line for line in src.splitlines() if line.startswith("FROM ") and "${MC_IMAGE}" not in line]
    assert froms and all("${IMAGE_PREFIX}" in f or "${PY_IMAGE}" in f for f in froms), froms
    assert "ARG IMAGE_PREFIX" in src and "ARG EXTRA_CA_PEM" in src
    assert "printf '%s\\n' \"$EXTRA_CA_PEM\" >> /etc/ssl/certs/ca-certificates.crt" in src


def test_compose_passes_the_network_build_args_and_prefixes_the_pulled_images() -> None:
    built = blocks = 0
    for name in COMPOSE_FILES:
        src = (ROOT / name).read_text(encoding="utf-8")
        built += src.count("    build:")
        for block in re.findall(r"\n      args:\n((?:        - .*\n)+)", src):
            blocks += 1
            for arg in (
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "NO_PROXY",
                "EXTRA_CA_PEM",
                "IMAGE_PREFIX",
                "PIP_INDEX_URL",
            ):
                assert f"        - {arg}\n" in block, (name, arg)
        assert "image: postgres" not in src and "image: ${HUB_EDGE_IMAGE:-caddy" not in src
    assert built == blocks  # every built service has the args block


def test_the_compose_download_uses_the_proxy_and_the_ca(tmp_path: Path) -> None:
    log = tmp_path / "curl.log"
    curl = 'echo "curl $*" >> "$FAKE_LOG"; exit 22'  # records the call, then "fails" (nothing is downloaded)
    docker = 'case "$1 ${2:-}" in "--version "*) echo "Docker version 27.0" ;; esac; exit 1'
    r = run_install(
        tmp_path,
        ("--yes", "--get-compose", *net_flags(tmp_path)),
        fakes={"curl": curl, "docker": docker},
        env_extra={"FAKE_LOG": str(log)},
    )
    call = log.read_text(encoding="utf-8")
    assert "github.com/docker/compose" in call and f"--proxy {PROXY}" in call and "--cacert" in call
    assert r.returncode == 1 and "download falhou" in r.stderr
