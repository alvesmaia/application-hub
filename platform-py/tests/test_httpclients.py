"""hub.httpclients: internal calls never go through a proxy; external ones use HUB_PROXY and the corporate CA."""

from __future__ import annotations

import ast
import ssl
from pathlib import Path
from typing import override

import certifi
import httpx
import pytest

from hub import httpclients as hc

ROOT = Path(__file__).resolve().parents[1]


class RecordingTransport(httpx.AsyncBaseTransport):
    """Named fake: answers 200 and keeps the hosts it was asked for."""

    def __init__(self) -> None:
        self.hosts: list[str] = []

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        return httpx.Response(200)


class RecordingSyncTransport(httpx.BaseTransport):
    def __init__(self) -> None:
        self.hosts: list[str] = []

    @override
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        return httpx.Response(200)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "HUB_PROXY",
        "HUB_NO_PROXY",
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HUB_EXTRA_CA_FILE", "/nao/existe/ca.pem")


@pytest.mark.parametrize(
    ("host", "direct"),
    [
        ("app-hub-objstore", True),  # compose service / container names have no dot
        ("app-hub-app-estoque__staging", True),
        ("localhost", True),
        ("127.0.0.1", True),
        ("10.231.4.7", True),  # private address: the corporate proxy never serves it
        ("repo.empresa.local", True),  # in HUB_NO_PROXY (.empresa.local)
        ("empresa.local", True),
        ("login.microsoftonline.com", False),
        ("api.github.com", False),
        ("20.190.1.1", False),  # public address
        ("evilempresa.local", False),  # suffix match is by label, not by text
    ],
)
def test_which_destinations_skip_the_proxy(host: str, direct: bool) -> None:
    assert hc.bypass(host, [".empresa.local"]) is direct


def test_no_proxy_star_and_cidr(monkeypatch: pytest.MonkeyPatch) -> None:
    assert hc.bypass("api.github.com", ["*"]) is True
    assert hc.bypass("20.190.1.1", ["20.190.0.0/16"]) is True


def test_hub_proxy_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy:3128")
    assert hc.proxy_url() == "http://env-proxy:3128"
    monkeypatch.setenv("HUB_PROXY", "http://proxy.empresa.local:8080")
    monkeypatch.setenv("NO_PROXY", "a.local")
    monkeypatch.setenv("HUB_NO_PROXY", ".empresa.local, 10.0.0.0/8")
    assert hc.proxy_url() == "http://proxy.empresa.local:8080"
    assert hc.no_proxy() == [".empresa.local", "10.0.0.0/8", "a.local"]


async def test_routing_sends_only_external_hosts_to_the_proxy() -> None:
    direct, proxied = RecordingTransport(), RecordingTransport()
    async with httpx.AsyncClient(transport=hc.AsyncRouting(direct, proxied, [".empresa.local"])) as c:
        for url in (
            "http://app-hub-objstore:9000/x",
            "https://login.microsoftonline.com/t",
            "https://repo.empresa.local/s",
        ):
            await c.get(url)
    assert direct.hosts == ["app-hub-objstore", "repo.empresa.local"]
    assert proxied.hosts == ["login.microsoftonline.com"]


async def test_without_a_proxy_everything_goes_direct() -> None:
    direct = RecordingTransport()
    async with httpx.AsyncClient(transport=hc.AsyncRouting(direct, None, [])) as c:
        await c.get("https://login.microsoftonline.com/t")
    assert direct.hosts == ["login.microsoftonline.com"]


def test_sync_routing_for_the_audit_webhook() -> None:
    direct, proxied = RecordingSyncTransport(), RecordingSyncTransport()
    with httpx.Client(transport=hc.SyncRouting(direct, proxied, [])) as c:
        c.post("https://ancora.empresa.com/head")
        c.post("http://app-hub-web:8090/x")
    assert proxied.hosts == ["ancora.empresa.com"] and direct.hosts == ["app-hub-web"]


def test_clients_ignore_the_proxy_variables_of_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # docker injects HTTP_PROXY into every container; an internal call must not follow it
    monkeypatch.setenv("HTTP_PROXY", "http://env-proxy:3128")
    assert hc.internal_client()._trust_env is False
    assert hc.external_client()._trust_env is False
    assert hc.external_sync_client()._trust_env is False


def test_a_given_transport_is_kept_for_the_tests() -> None:
    t = RecordingTransport()
    assert hc.external_client(transport=t)._transport is t


def test_corporate_ca_is_added_to_the_public_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    base = ssl.create_default_context(cafile=certifi.where()).cert_store_stats()["x509_ca"]
    assert hc.ssl_context().cert_store_stats()["x509_ca"] == base  # no extra CA file: certifi only
    extra = Path(certifi.where()).read_text(encoding="utf-8").split("-----END CERTIFICATE-----")[0]
    monkeypatch.setenv("HUB_EXTRA_CA_FILE", str(_write(extra + "-----END CERTIFICATE-----\n")))
    assert hc.ssl_context().cert_store_stats()["x509_ca"] >= base


def _write(text: str) -> Path:
    import tempfile

    f = Path(tempfile.mkdtemp()) / "ca.pem"
    f.write_text(text, encoding="utf-8")
    return f


def test_no_bare_httpx_client_outside_httpclients() -> None:
    # each new outbound call must choose internal_client or external_client (proxy, CA, no env proxy)
    offenders = []
    for f in (ROOT / "hub").rglob("*.py"):
        if f.name == "httpclients.py":
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("Client", "AsyncClient")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "httpx"
            ):
                offenders.append(f"{f.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def test_websocket_tunnel_to_the_apps_never_uses_a_proxy() -> None:
    src = (ROOT / "hub" / "gateway" / "ws.py").read_text(encoding="utf-8")
    assert "proxy=None" in src


def test_compose_mounts_the_corporate_ca_where_the_clients_read_it() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"${{HUB_CA_DIR:-./infra/ca-none}}:{Path(hc.CA_FILE).parent.as_posix()}:ro" in compose
    assert (ROOT / "infra" / "ca-none").is_dir()
