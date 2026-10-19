"""Apps built and run by the hub on a corporate network: build args, container environment, masked build log."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hub.ops import appnet
from hub.ops import runtime as rt

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "cli" / "hub_cli" / "templates" / "python-fastapi" / "Dockerfile"
PEM = "-----BEGIN CERTIFICATE-----\nMIIBfake\n-----END CERTIFICATE-----\n"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for k in ("HUB_PROXY", "HUB_NO_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY",
              "no_proxy", "HUB_PYPI_INDEX", "HUB_IMAGE_PREFIX"):  # fmt: skip
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HUB_EXTRA_CA_FILE", str(tmp_path / "sem-ca.pem"))


def corporate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "ca.pem").write_text(PEM, encoding="utf-8")
    monkeypatch.setenv("HUB_EXTRA_CA_FILE", str(tmp_path / "ca.pem"))
    monkeypatch.setenv("HUB_PROXY", "http://svc:s3nh4@proxy.empresa.local:8080")
    monkeypatch.setenv("HUB_NO_PROXY", ".empresa.local")
    monkeypatch.setenv("HUB_PYPI_INDEX", "https://repo.empresa.local/simple")
    monkeypatch.setenv("HUB_IMAGE_PREFIX", "docker.repo.empresa.local/library/")


def test_a_plain_install_sets_nothing() -> None:
    assert appnet.build_args() == {} and appnet.container_env() == {}


def test_build_args_carry_proxy_ca_and_mirrors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corporate(monkeypatch, tmp_path)
    a = appnet.build_args()
    assert a["HTTPS_PROXY"] == a["http_proxy"] == "http://svc:s3nh4@proxy.empresa.local:8080"
    assert (
        a["NO_PROXY"].split(",")[:3] == ["localhost", "127.0.0.1", "app-hub-web"]
        and ".empresa.local" in a["NO_PROXY"]
    )
    assert a["EXTRA_CA_PEM"] == PEM and a["IMAGE_PREFIX"] == "docker.repo.empresa.local/library/"
    assert a["PIP_INDEX_URL"] == a["UV_DEFAULT_INDEX"] == "https://repo.empresa.local/simple"


def test_app_containers_get_the_proxy_with_the_hub_names_excluded_and_the_ca(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    corporate(monkeypatch, tmp_path)
    env = appnet.container_env()
    assert env["HTTPS_PROXY"].endswith("@proxy.empresa.local:8080")
    for name in ("app-hub-web", "app-hub-objstore", "hub-web", "hub-objstore"):
        assert name in env["NO_PROXY"].split(","), name
    assert env["SSL_CERT_FILE"] == env["REQUESTS_CA_BUNDLE"] == "/etc/ssl/certs/ca-certificates.crt"
    assert "EXTRA_CA_PEM" not in env and "PIP_INDEX_URL" not in env


def test_the_build_log_never_shows_the_proxy_password() -> None:
    line = "Collecting x (from http://svc:s3nh4@proxy.empresa.local:8080/simple)"
    assert appnet.mask(line) == "Collecting x (from http://***@proxy.empresa.local:8080/simple)"


def test_build_stream_passes_the_build_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corporate(monkeypatch, tmp_path)
    seen: dict[str, Any] = {}
    info = SimpleNamespace(status_code=404, json=dict)
    api = SimpleNamespace(
        base_url="http+docker://t", _get=lambda url: info, build=lambda **kw: seen.update(kw) or iter(())
    )
    g = rt.Guarded(SimpleNamespace(api=api, containers=SimpleNamespace(get=lambda n: n)))
    g.build_stream(".", "app-hub-app-x:1", "Dockerfile", 1, buildargs=appnet.build_args())
    assert seen["buildargs"]["EXTRA_CA_PEM"] == PEM


def test_launch_adds_the_network_env_and_the_app_values_win(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    corporate(monkeypatch, tmp_path)
    made: dict[str, Any] = {}

    class FakeClient:
        def create(self, image: str, name: str, **kw: Any) -> Any:
            made.update(kw)
            return SimpleNamespace(start=lambda: None)

        def connect(self, *a: Any, **k: Any) -> None:
            return None

    eng = rt.Engine.__new__(rt.Engine)

    async def run(fn: Any, mutates: bool = False) -> Any:
        return fn(FakeClient())

    monkeypatch.setattr(eng, "_run", run)
    asyncio.run(eng.launch("app-hub-app-x", "img", {"NO_PROXY": "app-own"}, {}, {}, ["n"]))
    env = dict(e.split("=", 1) for e in made["environment"])
    assert env["NO_PROXY"] == "app-own" and env["SSL_CERT_FILE"].endswith("ca-certificates.crt")


def test_template_dockerfile_takes_the_mirror_and_the_ca_in_both_stages() -> None:
    src = TEMPLATE.read_text(encoding="utf-8")
    froms = [line for line in src.splitlines() if line.startswith("FROM ")]
    assert len(froms) == 2 and all(f.startswith("FROM ${IMAGE_PREFIX}python:") for f in froms)
    assert src.count("ARG EXTRA_CA_PEM") == 2  # builder (pip/uv) and final image (runtime trust)
    assert "ARG UV_DEFAULT_INDEX" in src and "ARG PIP_INDEX_URL" in src
    ca = r"""RUN if [ -n "$EXTRA_CA_PEM" ]; then printf '%s\n' "$EXTRA_CA_PEM" >> /etc/ssl/certs/ca-certificates.crt; fi"""
    assert src.count(ca) == 2, "the CA line must be whole (one line per stage)"
