"""HTTPS to the hub behind a corporate CA (TLS inspection): the CLI trusts the system's store and SSL_CERT_FILE,
not only certifi, and a refused certificate says so instead of a bare `ConnectError`."""

import datetime
import ssl
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from hub_cli import net


def corporate_ca(tmp: Path) -> Path:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Empresa Proxy CA")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))  # fmt: skip
    pem = tmp / "ca.pem"
    pem.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return pem


def subjects(ctx: ssl.SSLContext) -> set[str]:
    return {v for c in ctx.get_ca_certs() for rdn in c["subject"] for k, v in rdn if k == "commonName"}


def test_trusts_ssl_cert_file_and_keeps_public_cas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", str(corporate_ca(tmp_path)))
    found = subjects(net.tls())
    assert "Empresa Proxy CA" in found
    assert len(found) > 10  # the public CAs (certifi) are still there


def test_refused_certificate_explains_the_fix() -> None:
    cause = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate in certificate chain"
    )
    err = httpx.ConnectError("boom")
    err.__cause__ = cause
    msg = net.why(err)
    assert "self-signed certificate in certificate chain" in msg and "SSL_CERT_FILE" in msg
    assert net.why(httpx.ConnectTimeout("lento")) == "ConnectTimeout: lento"


def test_clients_use_the_shared_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}
    real = httpx.Client

    def spy(**kw: object) -> httpx.Client:
        seen.update(kw)
        return real(transport=httpx.MockTransport(lambda r: httpx.Response(200)))

    monkeypatch.setattr(httpx, "Client", spy)
    net.client(timeout=5)
    assert isinstance(seen["verify"], ssl.SSLContext)


def test_end_to_end_https_behind_a_corporate_ca(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A local HTTPS server whose certificate comes from a private CA: refused with the reason, accepted once
    SSL_CERT_FILE points to the CA (what an agent does behind TLS inspection)."""
    import http.server
    import ipaddress
    import threading

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Empresa Proxy CA")])
    now = datetime.datetime.now(datetime.UTC)
    day = datetime.timedelta(days=1)
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(now - day).not_valid_after(now + day)
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(ca_key, hashes.SHA256()))  # fmt: skip
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hub")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(2).not_valid_before(now - day)
            .not_valid_after(now + day)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(ca_key, hashes.SHA256()))  # fmt: skip
    (tmp_path / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "srv.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))  # fmt: skip
    srv = http.server.HTTPServer(("127.0.0.1", 0), http.server.SimpleHTTPRequestHandler)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(tmp_path / "srv.pem")
    srv.socket = sctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"https://127.0.0.1:{srv.server_address[1]}/"
    try:
        monkeypatch.delenv("SSL_CERT_FILE", raising=False)
        with pytest.raises(httpx.ConnectError) as refused, net.client(timeout=5) as c:
            c.get(url)
        assert "SSL_CERT_FILE" in net.why(refused.value)
        monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ca.pem"))
        with net.client(timeout=5) as c:
            assert c.get(url).status_code in (200, 404)
    finally:
        srv.shutdown()
