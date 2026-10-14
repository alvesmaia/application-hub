"""Graph simulator (`graphsim`) and SharePoint authentication by certificate (JWT client assertion)."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import socket
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from admin_helper import make_hub
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from graphsim.sim import Sim, SimConfig, make_app
from hub.storage import Body, StorageError
from hub.storage.cert import ClientCert, verify_assertion
from hub.storage.graph import Endpoints, GraphClient, SharePointConfig

CFG = SimConfig()
SECRET_ENV = "tenant-simulador"


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def feed(data: bytes, step: int = 100_000) -> Body:
    async def gen() -> AsyncIterator[bytes]:
        for i in range(0, len(data), step):
            yield data[i : i + step]

    return Body(gen())


def make_cert(cn: str = "hub-teste", days: int = 30) -> tuple[Any, bytes]:
    key = rsa.generate_private_key(65537, 2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=days))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.DER)


def pem_of(key: Any, der: bytes, pkcs1: bool = False) -> str:
    fmt = serialization.PrivateFormat.TraditionalOpenSSL if pkcs1 else serialization.PrivateFormat.PKCS8
    kp = key.private_bytes(serialization.Encoding.PEM, fmt, serialization.NoEncryption()).decode()
    cp = x509.load_der_x509_certificate(der).public_bytes(serialization.Encoding.PEM).decode()
    return cp + str(kp)


@pytest.fixture(scope="module")
def pair() -> tuple[Any, bytes]:
    return make_cert()


@pytest.fixture
def sim() -> Sim:
    return Sim(SimConfig())


def client(
    sim: Sim, cert: ClientCert | None = None, secret: str = CFG.client_secret, **kw: Any
) -> GraphClient:
    ep = Endpoints("http://sim", "http://sim/v1.0")
    cfg = SharePointConfig(
        kw.pop("tenant", CFG.tenant),
        kw.pop("client_id", CFG.client_id),
        secret,
        "https://contoso.sharepoint.com/sites/Hub",
        drive=kw.pop("drive", "Documentos"),
        base_folder=kw.pop("base", "Base"),
        cert=cert,
    )
    c = GraphClient(ep, cfg, httpx.ASGITransport(app=make_app(sim)))
    c.chunk, c.simple_max = 320 * 1024, 1000
    return c


# ---- Graph against the simulator ----


async def test_graph_crud_and_token_cache(sim: Sim) -> None:
    c = client(sim)
    item = await c.upload("a.txt", 5, "text/plain", feed(b"hello"))
    assert item.name == "a.txt" and sim.file("Documentos", "Base/a.txt") == b"hello"
    assert (await c.meta("a.txt")).size == 5
    _, _, stream = await c.download("a.txt")
    assert b"".join([x async for x in stream]) == b"hello"
    assert sim.stats["downloads"] == 1 and sim.stats["downloads_with_auth"] == 0
    assert [i.name for i in await c.ls("")] == ["a.txt"]
    assert (await c.move("a.txt", "b.txt")).path == "b.txt"
    await c.delete("b.txt")
    with pytest.raises(StorageError) as e:
        await c.delete("b.txt")
    assert e.value.kind == "not_found"
    assert sim.stats["tokens_issued"] == 1  # the token is reused across calls
    with pytest.raises(StorageError):
        await c.upload("../x", 1, "text/plain", feed(b"x"))
    await c.aclose()


async def test_graph_large_upload_uses_session_in_chunks(sim: Sim) -> None:
    c = client(sim)
    data = bytes(range(256)) * 2800  # ~700 KiB: 3 chunks of 320 KiB (the last one smaller)
    item = await c.upload("big.bin", len(data), "application/octet-stream", feed(data))
    assert item.size == len(data) and sim.file("Documentos", "Base/big.bin") == data
    assert sim.stats["sessions"] == 1 and sim.stats["chunks"] == 3
    with pytest.raises(StorageError, match="maior que o tamanho"):
        await client(sim).upload("c.txt", 3, "text/plain", feed(b"toolong"))
    with pytest.raises(StorageError, match="terminou antes"):
        await client(sim).upload("d.txt", 9, "text/plain", feed(b"abc"))


async def test_graph_listing_paginates(sim: Sim) -> None:
    c = client(sim)
    for i in range(5):
        await c.upload(f"f{i}.txt", 1, "text/plain", feed(b"x"))
    tok = await c.token()
    drive = next(iter(sim.drives))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_app(sim)), base_url="http://sim"
    ) as h:
        r = await h.get(
            f"/v1.0/drives/{drive}/root:/Base:/children?$top=2", headers={"authorization": f"Bearer {tok}"}
        )
        page = r.json()
        assert len(page["value"]) == 2 and "skiptoken=2" in page["@odata.nextLink"]
        nxt = await h.get(
            page["@odata.nextLink"].replace("http://sim", ""), headers={"authorization": f"Bearer {tok}"}
        )
        assert len(nxt.json()["value"]) == 2
    assert len(await c.ls("")) == 5


async def test_graph_share_link_enforces_expiry(sim: Sim) -> None:
    c = client(sim)
    await c.upload("a.txt", 5, "text/plain", feed(b"hello"))
    link = await c.share_link("a.txt", "view", "organization", 3600)
    assert link["url"].startswith("https://contoso.sharepoint.com/") and link["expires"]
    assert len(sim.links) == 1
    sim.cfg.link_expiry = "ignore"  # the tenant ignored the validity: the link is deleted and refused
    with pytest.raises(StorageError, match="foi apagado") as e:
        await c.share_link("a.txt", "view", "organization", 3600)
    assert e.value.kind == "upstream" and len(sim.links) == 1
    with pytest.raises(StorageError):
        await c.share_link("a.txt", "bogus", "organization", 3600)


async def test_graph_test_steps_and_failures(sim: Sim) -> None:
    steps = await client(sim).test()
    assert [s["key"] for s in steps] == ["token", "site", "drive", "folder", "write", "read", "delete"]
    assert all(s["ok"] for s in steps) and not [p for p in sim.paths("Documentos") if "sonda" in p]
    bad = await client(sim, secret="errado-errado").test()
    assert bad[0]["ok"] is False and all(s["ok"] is None for s in bad[1:])
    sim.cfg.deny_site = True
    denied = await client(sim).test()
    assert denied[1]["ok"] is False


@pytest.mark.parametrize(
    ("kw", "kind", "text"),
    [
        ({"tenant": "outro-tenant"}, "invalid", "Tenant não encontrado"),
        ({"client_id": "99999999-2222-3333-4444-555555555555"}, "auth", "Client ID não encontrado"),
    ],
)
async def test_sim_token_errors_map_to_readable_messages(
    sim: Sim, kw: dict[str, Any], kind: str, text: str
) -> None:
    with pytest.raises(StorageError) as e:
        await client(sim, **kw).ls("")
    assert e.value.kind == kind and text in e.value.message


async def test_graph_throttle_retry_and_revoked_token(sim: Sim) -> None:
    c = client(sim)
    await c.upload("a.txt", 1, "text/plain", feed(b"x"))
    sim.cfg.throttle_next, sim.cfg.throttle_retry_after = 1, 0
    assert (await c.meta("a.txt")).size == 1 and sim.stats["throttled"] == 1
    sim.tokens.clear()  # token invalidated: a 401 renews the token and retries
    assert (
        (await c.meta("a.txt")).size == 1
        and sim.stats["tokens_issued"] == 2
        and sim.stats["unauthorized"] == 1
    )


async def test_sim_control_endpoints_and_misc(sim: Sim) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_app(sim)), base_url="http://sim"
    ) as h:
        assert (await h.get("/_health")).text == "ok" and (await h.get("/_sim/health")).text == "ok"
        assert (await h.post("/_sim/config", json={"site_name": "X", "latency_ms": 1})).json()[
            "site_name"
        ] == "X"
        assert (await h.post("/_sim/config", json={"nope": 1})).status_code == 400
        assert (await h.post("/_sim/config", content=b"{")).status_code == 400
        assert (await h.get("/_sim/stats")).json()["requests"] >= 3
        assert (await h.post("/_sim/reset")).status_code == 204
        assert (await h.get("/_sim/nope")).status_code == 404
        assert (await h.get("/x")).status_code == 404 and (await h.get("/v1.0/sites/x")).status_code == 401
        sim.cfg.control = False
        assert (await h.get("/_sim/stats")).status_code == 404
        r = await h.post("/oauth/oauth2/v2.0/token", data={"grant_type": "x"})
        assert r.status_code == 400
        r = await h.post(f"/{CFG.tenant}/oauth2/v2.0/token", data={"grant_type": "x"})
        assert r.json()["error"] == "unsupported_grant_type"
        r = await h.post(
            f"/{CFG.tenant}/oauth2/v2.0/token", data={"grant_type": "client_credentials", "scope": "x"}
        )
        assert r.json()["error"] == "invalid_scope"


def test_sim_config_from_env(tmp_path: Path) -> None:
    pem = tmp_path / "c.pem"
    pem.write_text("PEM")
    c = SimConfig.from_env(
        {
            "GRAPHSIM_TENANT": "t",
            "GRAPHSIM_DRIVES": "A, B",
            "GRAPHSIM_THROTTLE_EVERY": "5",
            "GRAPHSIM_MAX_MB": "2",
            "GRAPHSIM_FAIL_RATE": "0.5",
            "GRAPHSIM_CONTROL": "0",
            "GRAPHSIM_CERT_PEM_FILE": str(pem),
        }
    )
    assert (c.tenant, c.drives, c.throttle_every, c.max_bytes, c.fail_rate, c.control, c.cert_pem) == (
        "t",
        ["A", "B"],
        5,
        2 << 20,
        0.5,
        False,
        "PEM",
    )
    assert SimConfig.from_env({"GRAPHSIM_CERT_PEM_FILE": str(tmp_path / "nao")}).cert_pem == ""


async def test_sim_fail_rate_and_tree_operations(sim: Sim) -> None:
    c = client(sim)
    await c.upload("pasta/sub/a.txt", 1, "text/plain", feed(b"x"))
    assert "Base/pasta/sub" in sim.paths("Documentos")
    await c.move("pasta/sub/a.txt", "z.txt")
    await c.delete("pasta")
    assert sim.file("Documentos", "Base/z.txt") == b"x" and "Base/pasta" not in sim.paths("Documentos")
    sim.cfg.fail_rate = 1.0
    with pytest.raises(StorageError) as e:
        await c.meta("z.txt")
    assert e.value.kind == "unavailable" and sim.stats["failed"] >= 1


# ---- certificado -------------------------------------------------------------------------------


def test_cert_parse_pem_pkcs1_pfx_and_stored_roundtrip(pair: tuple[Any, bytes]) -> None:
    key, der = pair
    c1 = ClientCert.parse(pem_of(key, der).encode())
    c2 = ClientCert.parse(pem_of(key, der, pkcs1=True).encode())
    pfx = pkcs12.serialize_key_and_certificates(
        b"x",
        key,
        x509.load_der_x509_certificate(der),
        None,
        serialization.BestAvailableEncryption(b"senha-pfx"),
    )
    c3 = ClientCert.parse(pfx, "senha-pfx")
    assert c1.thumbprint == c2.thumbprint == c3.thumbprint and len(c1.thumbprint) == 40
    assert c1.subject == "hub-teste" and c1.not_after and c1.not_after > time.time()
    c4 = ClientCert.from_stored(c1.to_stored())
    assert c4.thumbprint == c1.thumbprint
    assert "PRIVATE" not in repr(c1) and "key" not in repr(c1).lower().replace("thumbprint", "")
    with pytest.raises(StorageError, match="PFX"):
        ClientCert.parse(pfx, "errada")
    with pytest.raises(StorageError, match="ilegível"):
        ClientCert.from_stored("lixo")
    with pytest.raises(StorageError, match="ilegível"):
        ClientCert.from_stored("a.b")


def test_cert_rejects_bad_inputs(pair: tuple[Any, bytes]) -> None:
    key, der = pair
    other, other_der = make_cert("outro")
    pem = pem_of(key, der)
    with pytest.raises(StorageError, match="BEGIN CERTIFICATE"):
        ClientCert.parse(pem[pem.index("-----BEGIN PRIVATE") :].encode().replace(b"PRIVATE", b"PRIVATE", 1))
    with pytest.raises(StorageError, match="chave privada"):
        ClientCert.parse(pem.split("-----BEGIN PRIVATE")[0].encode())
    with pytest.raises(StorageError, match="não corresponde"):
        ClientCert.parse(
            (
                pem_of(key, der).split("-----BEGIN PRIVATE")[0]
                + pem_of(other, other_der).split("-----END CERTIFICATE-----\n")[1]
            ).encode()
        )
    with pytest.raises(StorageError, match="ilegível"):
        ClientCert.parse(
            b"-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----"
        )
    with pytest.raises(StorageError, match="PFX"):
        ClientCert.parse(b"nao e pfx")
    with pytest.raises(StorageError, match="inválido"):
        ClientCert.from_parts(key, b"nao-der")
    small = rsa.generate_private_key(65537, 1024)  # noqa: S505
    with pytest.raises(StorageError, match="2048"):
        ClientCert.from_parts(small, der)


def test_assertion_is_verifiable_and_tamper_proof(pair: tuple[Any, bytes]) -> None:
    key, der = pair
    cert = ClientCert.parse(pem_of(key, der).encode())
    jwt = cert.assertion("cid", "http://sim/t/oauth2/v2.0/token")
    claims = verify_assertion(jwt, der)
    assert claims and claims["iss"] == claims["sub"] == "cid" and claims["exp"] > time.time()
    assert verify_assertion(jwt + "x", der) is None and verify_assertion("a.b.c", der) is None
    assert verify_assertion(jwt, make_cert("outro")[1]) is None
    h, c, s = jwt.split(".")
    assert verify_assertion(f"{h}.{c[:-2]}AA.{s}", der) is None


async def test_graph_with_certificate_against_sim(sim: Sim, pair: tuple[Any, bytes]) -> None:
    key, der = pair
    sim.cfg.cert_pem = pem_of(key, der)
    cert = ClientCert.parse(sim.cfg.cert_pem.encode())
    c = client(sim, cert=cert, secret="")
    steps = await c.test()
    assert all(s["ok"] for s in steps), steps
    assert sim.stats["assertions"] == 1
    # certificate not registered in the app registration: refused, without leaking anything
    other, other_der = make_cert("outro")
    bad = client(sim, cert=ClientCert.parse(pem_of(other, other_der).encode()), secret="")
    with pytest.raises(StorageError) as e:
        await bad.ls("")
    assert e.value.kind == "auth" and "Credencial recusada" in e.value.message
    sim.cfg.cert_pem = ""  # no certificate configured at the provider: nothing passes
    with pytest.raises(StorageError):
        await client(sim, cert=cert, secret="").ls("")


# ---- connections in the hub (API) against a real simulator over HTTP ----


@pytest.fixture
def live_sim() -> Iterator[tuple[Sim, dict[str, str]]]:
    s = Sim(SimConfig())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(make_app(s), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    env = {
        "HUB_GRAPH_URL": f"http://127.0.0.1:{port}/v1.0",
        "HUB_GRAPH_LOGIN_URL": f"http://127.0.0.1:{port}",
    }
    yield s, env
    srv.should_exit = True
    t.join(5)


def sp_body(name: str, **kw: Any) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "sharepoint",
        "tenant_id": CFG.tenant,
        "client_id": CFG.client_id,
        "site": "https://contoso.sharepoint.com/sites/Hub",
        "drive": "Documentos",
        **kw,
    }


def test_connection_test_with_secret_and_with_certificate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    live_sim: tuple[Sim, dict[str, str]],
    pair: tuple[Any, bytes],
) -> None:
    key, der = pair
    pem = pem_of(key, der)
    sim_, env = live_sim
    sim_.cfg.cert_pem = pem
    hub: TestClient = make_hub(tmp_path, monkeypatch)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert (
        hub.post("/api/admin/connections", json=sp_body("sp-secret", secret=CFG.client_secret)).status_code
        == 201
    )
    r = hub.post("/api/admin/connections/sp-secret/test", json={}).json()
    assert r["ok"] is True, r
    # certificate: PEM (cert + key) in base64; never comes back in the API/HTML
    b64 = base64.b64encode(pem.encode()).decode()
    created = hub.post(
        "/api/admin/connections", json=sp_body("sp-cert", auth="certificate", certificate_b64=b64)
    )
    assert created.status_code == 201
    view = created.json()
    assert view["auth"] == "certificate" and view["cert_thumbprint"] and view["cert_subject"] == "hub-teste"
    r = hub.post("/api/admin/connections/sp-cert/test", json={}).json()
    assert r["ok"] is True and sim_.stats["assertions"] >= 1, r
    # no key/certificate in HTML, API, audit, log or clear text on disk
    leaks = ["PRIVATE KEY", b64[:40], pem.split("\n")[1]]
    blob = hub.get("/admin/connections").text + hub.get("/api/admin/connections").text
    audit = str(run(hub.app.state.admin.cfg.read())["audit"])  # type: ignore[attr-defined]
    on_disk = "".join(p.read_text(errors="ignore") for p in tmp_path.rglob("*.json"))
    for leak in leaks:
        assert leak not in blob and leak not in on_disk and leak not in audit
    # no file when switching to a certificate, and certificate rotation
    r = hub.put("/api/admin/connections/sp-secret", json=sp_body("sp-secret", auth="certificate"))
    assert r.status_code == 400 and "Envie o certificado" in r.json()["error"]
    assert hub.post("/api/admin/connections/sp-cert/rotate", json={}).status_code == 400
    r = hub.post("/api/admin/connections/sp-cert/rotate", json={"certificate_b64": b64})
    assert r.status_code == 200 and r.json()["last_test"] is None
    bad = hub.post(
        "/api/admin/connections/sp-cert/rotate", json={"certificate_b64": base64.b64encode(b"lixo").decode()}
    )
    assert bad.status_code == 400
    # back to secret: the stored certificate disappears
    r = hub.put(
        "/api/admin/connections/sp-cert", json=sp_body("sp-cert", auth="secret", secret=CFG.client_secret)
    )
    assert r.status_code == 200 and r.json()["cert_thumbprint"] == ""
    assert hub.post("/api/admin/connections/sp-cert/test", json={}).json()["ok"] is True


def test_certificate_input_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pair: tuple[Any, bytes]
) -> None:
    hub: TestClient = make_hub(tmp_path, monkeypatch)
    key, der = pair
    pem = pem_of(key, der)
    b = lambda **k: sp_body("sp-x", auth="certificate", **k)  # noqa: E731
    assert "Envie o certificado" in hub.post("/api/admin/connections", json=b()).json()["error"]
    assert hub.post("/api/admin/connections", json=b(certificate_b64="@@@")).status_code == 400
    big = base64.b64encode(b"x" * 70_000).decode()
    assert "grande demais" in hub.post("/api/admin/connections", json=b(certificate_b64=big)).json()["error"]
    old = rsa.generate_private_key(65537, 2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "velho")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(old.public_key())
        .serial_number(1)
        .not_valid_before(now - dt.timedelta(days=9))
        .not_valid_after(now - dt.timedelta(days=1))
        .sign(old, hashes.SHA256())
    )
    expired_pem = pem_of(old, cert.public_bytes(serialization.Encoding.DER))
    r = hub.post(
        "/api/admin/connections", json=b(certificate_b64=base64.b64encode(expired_pem.encode()).decode())
    )
    assert "vencido" in r.json()["error"]
    assert (
        "secret ou certificate"
        in hub.post("/api/admin/connections", json=sp_body("sp-y", auth="x")).json()["error"]
    )
    ok = hub.post("/api/admin/connections", json=b(certificate_b64=base64.b64encode(pem.encode()).decode()))
    assert ok.status_code == 201 and "PRIVATE" not in ok.text
