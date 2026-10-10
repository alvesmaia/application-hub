"""Armazenamento do broker: caminhos confinados, URL pré-assinada (vetores do Rust), S3 e Graph com transporte simulado."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from hub.storage import Body, StorageError, clean, clean_file, join, parse_rfc3339, redact, rfc3339
from hub.storage.graph import Endpoints, GraphClient, SharePointConfig, valid_guid, valid_site, valid_tenant
from hub.storage.s3 import S3Client, S3Config, blocked_ip

VECTORS = json.loads((Path(__file__).parent / "vectors" / "storage-vectors.json").read_text(encoding="utf-8"))
GUID = "11111111-2222-3333-4444-555555555555"


def feed(data: bytes, step: int = 7) -> Body:
    async def gen() -> AsyncIterator[bytes]:
        for i in range(0, len(data), step):
            yield data[i : i + step]

    return Body(gen())


def test_path_vectors_and_limits() -> None:
    for raw, expected in VECTORS["paths_ok"]:
        assert clean(raw) == expected
    for bad in VECTORS["paths_bad"]:
        with pytest.raises(StorageError):
            clean(bad)
    with pytest.raises(StorageError):
        clean("a/" * 40)
    with pytest.raises(StorageError):
        clean("x" * 300)
    with pytest.raises(StorageError):
        clean_file("")
    assert join("base", "x") == "base/x"
    assert join("", "x") == "x"
    assert join("base", "") == "base"


def test_time_and_redaction_helpers() -> None:
    assert rfc3339(0) == "1970-01-01T00:00:00Z"
    assert parse_rfc3339(rfc3339(1_790_596_800)) == 1_790_596_800
    assert parse_rfc3339("2026-09-28T09:00:00-03:00") == 1_790_596_800
    assert parse_rfc3339("2026-09-28T12:00:00.1234567Z") == 1_790_596_800
    for bad in ("", "2026-09-28", "2026-13-01T00:00:00Z", "2026-09-28T12:00:00"):
        assert parse_rfc3339(bad) is None
    assert redact("falhou com s3cr3t-valor no corpo", ["s3cr3t-valor", ""]) == "falhou com •••• no corpo"


def test_validators() -> None:
    assert valid_guid(GUID)
    assert not valid_guid("nope")
    assert valid_tenant("contoso.onmicrosoft.com") and not valid_tenant("a b")
    assert valid_site("https://contoso.sharepoint.com/sites/x") and valid_site("contoso.sharepoint.com,a,b")
    assert not valid_site("https://x.com/a?b") and not valid_site("")


def s3(handler: Any = None, **kw: Any) -> S3Client:
    cfg = S3Config(
        kw.get("endpoint", "http://objstore:9000"),
        kw.get("bucket", "docs"),
        "AK",
        "SECRETKEY123",
        base_folder=kw.get("base", "base"),
    )
    return S3Client(cfg, httpx.MockTransport(handler or (lambda r: httpx.Response(404))))


def test_presign_matches_rust_vector() -> None:
    p = VECTORS["presign"]
    cfg = S3Config(
        p["endpoint"], p["bucket"], p["access_key"], p["secret_key"], p["region"], public_url=p["public_url"]
    )
    c = S3Client(cfg)
    now = datetime.fromtimestamp(p["now"], UTC)
    assert c.presign("GET", p["key"], p["expires"], now) == p["url"]
    assert c.presign("PUT", p["key"], p["expires"], now) == p["put_url"]


def test_s3_endpoint_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    for host in ("169.254.169.254", "0.0.0.0", "[fd00:ec2::254]", "[::ffff:169.254.169.254]"):  # noqa: S104
        with pytest.raises(StorageError, match="bloqueado"):
            S3Client(S3Config(f"http://{host}", "docs", "a", "b"))
    assert not blocked_ip(__import__("ipaddress").ip_address("10.0.0.5"))
    monkeypatch.setenv("HUB_STORAGE_ALLOW_HOSTS", "169.254.169.254")
    S3Client(S3Config("http://169.254.169.254", "docs", "a", "b"))
    with pytest.raises(StorageError):
        S3Client(S3Config("ftp://x", "docs", "a", "b"))
    with pytest.raises(StorageError):
        S3Client(S3Config("http://x", "Bad_Bucket", "a", "b"))


async def test_s3_operations_are_signed_and_confined(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUB_STORAGE_ALLOW_HOSTS", "objstore")
    seen: list[httpx.Request] = []
    store: dict[str, bytes] = {}

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r)
        key = r.url.path.removeprefix("/docs/")
        assert r.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AK/")
        assert "SECRETKEY123" not in str(r.headers) and "SECRETKEY123" not in str(r.url)
        if r.method == "PUT" and "x-amz-copy-source" in r.headers:
            store[key] = store[r.headers["x-amz-copy-source"].removeprefix("/docs/")]
            return httpx.Response(200)
        if r.method == "PUT":
            store[key] = r.content
            return httpx.Response(200)
        if r.method == "DELETE":
            store.pop(key, None)
            return httpx.Response(204)
        if r.method == "HEAD":
            return (
                httpx.Response(200, headers={"content-length": str(len(store[key])), "etag": '"e"'})
                if key in store
                else httpx.Response(404)
            )
        if r.url.query and b"list-type" in r.url.query:
            xml = (
                "<R><CommonPrefixes><Prefix>base/sub/</Prefix></CommonPrefixes>"
                "<Contents><Key>base/a&amp;b.txt</Key><Size>3</Size><LastModified>2026-01-01T00:00:00Z</LastModified></Contents></R>"
            )
            return httpx.Response(200, text=xml)
        return (
            httpx.Response(200, content=store[key], headers={"content-type": "text/plain"})
            if key in store
            else httpx.Response(404)
        )

    c = s3(handler)
    item = await c.upload("x/a.txt", 5, "text/plain", feed(b"hello"))
    assert (item.path, item.size) == ("x/a.txt", 5) and store["base/x/a.txt"] == b"hello"
    assert (await c.meta("x/a.txt")).size == 5
    ctype, length, stream = await c.download("x/a.txt")
    assert (ctype, length, b"".join([x async for x in stream])) == ("text/plain", 5, b"hello")
    moved = await c.move("x/a.txt", "y/b.txt")
    assert moved.path == "y/b.txt" and "base/x/a.txt" not in store
    items = await c.ls("")
    assert {(i.name, i.folder) for i in items} == {("sub", True), ("a&b.txt", False)}
    link = await c.share_link("y/b.txt", "view", "anonymous", 3600)
    assert "X-Amz-Signature=" in link["url"] and "SECRETKEY123" not in link["url"]
    with pytest.raises(StorageError, match="só há link de leitura"):
        await c.share_link("y/b.txt", "edit", "anonymous", 3600)
    await c.delete("y/b.txt")
    with pytest.raises(StorageError) as e:
        await c.meta("y/b.txt")
    assert e.value.kind == "not_found"
    with pytest.raises(StorageError):
        await c.download("../escape")
    assert all("escape" not in str(r.url) for r in seen)
    steps = await c.test()
    assert [s["key"] for s in steps] == ["bucket", "write", "read", "delete"] and all(s["ok"] for s in steps)
    await c.aclose()


@pytest.mark.parametrize(
    ("status", "kind", "retry"),
    [
        (403, "auth", None),
        (404, "not_found", None),
        (409, "conflict", None),
        (413, "too_large", None),
        (503, "unavailable", 7),
        (418, "upstream", None),
    ],
)
async def test_s3_error_mapping(status: int, kind: str, retry: int | None) -> None:
    c = s3(lambda r: httpx.Response(status, headers={"retry-after": "7"} if retry else {}))
    with pytest.raises(StorageError) as e:
        await c.ls("")
    assert e.value.kind == kind and e.value.retry_after == retry


async def test_s3_unreachable_is_unavailable() -> None:
    def boom(r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("x")

    with pytest.raises(StorageError) as e:
        await s3(boom).ls("")
    assert e.value.kind == "unavailable" and e.value.status == 503


# ---- Graph: mapeamento de erros do token (o fluxo completo roda contra o simulador: test_graphsim.py) ----


def graph(handler: Any) -> GraphClient:
    ep = Endpoints("http://graph.test/login", "http://graph.test/v1.0")
    cfg = SharePointConfig(
        "tenant.example", GUID, "SECRET-VALUE", "https://contoso.sharepoint.com/sites/docs"
    )
    return GraphClient(ep, cfg, httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("body", "kind", "text"),
    [
        (
            {"error": "invalid_request", "error_description": "AADSTS90002: Tenant 'x' not found"},
            "invalid",
            "Tenant não encontrado",
        ),
        (
            {"error": "unauthorized_client", "error_description": "AADSTS700016: app"},
            "auth",
            "Client ID não encontrado",
        ),
        (
            {"error": "invalid_client", "error_description": "AADSTS7000222: expired"},
            "auth",
            "segredo do cliente expirou",
        ),
        ({"error": "invalid_client", "error_description": "x"}, "auth", "Credencial recusada"),
        ({"error": "server_error"}, "upstream", "recusou o pedido de token"),
    ],
)
async def test_graph_token_errors_are_readable_and_hide_secret(
    body: dict[str, str], kind: str, text: str
) -> None:
    with pytest.raises(StorageError) as e:
        await graph(lambda r: httpx.Response(400, json=body)).ls("")
    assert e.value.kind == kind and text in e.value.message and "SECRET-VALUE" not in e.value.admin_text()


def test_endpoints_default_and_pinning(monkeypatch: pytest.MonkeyPatch) -> None:
    ep = Endpoints.from_env()
    assert ep.is_default()
    assert ep.content_url_ok("https://contoso.sharepoint.com/x", "https://contoso.sharepoint.com/sites/a")
    assert ep.content_url_ok("https://graph.microsoft.com/v1.0/x", "") is True
    assert not ep.content_url_ok("https://evil.example/x", "https://contoso.sharepoint.com/sites/a")
    assert not ep.content_url_ok("http://contoso.sharepoint.com/x", "")
    monkeypatch.setenv("HUB_GRAPH_URL", "http://sim:9/v1.0")
    sim = Endpoints.from_env()
    assert not sim.is_default() and sim.content_url_ok("http://sim:9/upload/1", "")
    assert not sim.content_url_ok("http://other:9/upload/1", "")
