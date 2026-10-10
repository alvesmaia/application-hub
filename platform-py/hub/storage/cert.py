"""Certificado do app registration (Entra ID): asserção de cliente JWT RS256 (RFC 7523).

Aceita PEM (certificado + chave privada sem senha) ou PFX/PKCS#12 (com senha). Só a chave (PKCS#8) e
o certificado (DER) são guardados, cifrados no cofre pela camada de conexões; a senha do PFX não.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from hub.storage import StorageError

_b64u = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
_unb64u = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))  # noqa: E731
_KEY_PEM = re.compile(
    r"-----BEGIN (?:RSA |ENCRYPTED )?PRIVATE KEY-----.*?-----END (?:RSA |ENCRYPTED )?PRIVATE KEY-----", re.S
)
_CERT_PEM = re.compile(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)


def _err(msg: str) -> StorageError:
    return StorageError("invalid", msg)


@dataclass
class ClientCert:
    key: rsa.RSAPrivateKey = field(repr=False)
    cert_der: bytes = field(repr=False)
    thumbprint: str = ""
    not_after: int | None = None
    subject: str = ""

    @classmethod
    def from_parts(cls, key: Any, cert_der: bytes) -> ClientCert:
        if not isinstance(key, rsa.RSAPrivateKey) or not 2048 <= key.key_size <= 8192:
            raise _err("Chave privada inválida (só RSA de 2048 a 8192 bits).")
        try:
            cert = x509.load_der_x509_certificate(cert_der)
        except ValueError as e:
            raise _err("Certificado X.509 inválido.") from e
        pub = cert.public_key()
        if not isinstance(pub, rsa.RSAPublicKey) or pub.public_numbers() != key.public_key().public_numbers():
            raise _err("A chave privada não corresponde ao certificado.")
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        return cls(
            key,
            cert_der,
            hashlib.sha1(cert_der).hexdigest().upper(),  # noqa: S324 - impressão digital do Entra
            int(cert.not_valid_after_utc.timestamp()),
            str(cn[0].value) if cn else "",
        )

    @classmethod
    def parse(cls, data: bytes, password: str = "") -> ClientCert:
        if b"-----BEGIN" in data[:4096]:
            return cls.from_pem(data.decode("utf-8", "replace"))
        try:
            key, cert, _ = pkcs12.load_key_and_certificates(data, password.encode() or None)
        except (ValueError, TypeError) as e:
            raise _err("PFX ilegível ou senha incorreta (algoritmo legado não é aceito).") from e
        if key is None or cert is None:
            raise _err("O PFX precisa ter o certificado e a chave privada.")
        return cls.from_parts(key, cert.public_bytes(serialization.Encoding.DER))

    @classmethod
    def from_pem(cls, pem: str) -> ClientCert:
        k, c = _KEY_PEM.search(pem), _CERT_PEM.search(pem)
        if not c:
            raise _err("O PEM precisa conter o certificado (BEGIN CERTIFICATE).")
        if not k:
            raise _err("O PEM precisa conter a chave privada (sem senha).")
        try:
            key = serialization.load_pem_private_key(k.group().encode(), None)
            cert = x509.load_pem_x509_certificate(c.group().encode())
        except (ValueError, TypeError) as e:
            raise _err("PEM ilegível (a chave privada não pode ter senha).") from e
        return cls.from_parts(key, cert.public_bytes(serialization.Encoding.DER))

    def to_stored(self) -> str:
        pk8 = self.key.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        return f"{base64.b64encode(pk8).decode()}.{base64.b64encode(self.cert_der).decode()}"

    @classmethod
    def from_stored(cls, s: str) -> ClientCert:
        try:
            k, c = s.split(".", 1)
            key = serialization.load_der_private_key(base64.b64decode(k), None)
            return cls.from_parts(key, base64.b64decode(c))
        except (ValueError, TypeError) as e:
            raise _err("certificado guardado ilegível") from e

    def assertion(self, client_id: str, token_url: str, now: int | None = None) -> str:
        now = int(time.time()) if now is None else now
        header = {
            "alg": "RS256",
            "typ": "JWT",
            "x5t": _b64u(hashlib.sha1(self.cert_der).digest()),  # noqa: S324
            "x5t#S256": _b64u(hashlib.sha256(self.cert_der).digest()),
        }
        claims = {
            "aud": token_url,
            "iss": client_id,
            "sub": client_id,
            "jti": secrets.token_hex(16),
            "nbf": now - 60,
            "iat": now,
            "exp": now + 600,
        }
        si = f"{_b64u(json.dumps(header).encode())}.{_b64u(json.dumps(claims).encode())}"
        sig = self.key.sign(si.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{si}.{_b64u(sig)}"


def pem_cert_der(pem: str) -> bytes | None:
    m = _CERT_PEM.search(pem)
    if not m:
        return None
    return x509.load_pem_x509_certificate(m.group().encode()).public_bytes(serialization.Encoding.DER)


def verify_assertion(jwt: str, cert_der: bytes) -> dict[str, Any] | None:
    """Confere assinatura RS256 e `x5t` com o certificado; devolve as claims (simulador e testes)."""
    try:
        si, sig = jwt.rsplit(".", 1)
        h, c = si.split(".", 1)
        pub = x509.load_der_x509_certificate(cert_der).public_key()
        if not isinstance(pub, rsa.RSAPublicKey):
            return None
        pub.verify(_unb64u(sig), si.encode(), padding.PKCS1v15(), hashes.SHA256())
        if json.loads(_unb64u(h)).get("x5t") != _b64u(hashlib.sha1(cert_der).digest()):  # noqa: S324
            return None
        claims = json.loads(_unb64u(c))
    except (ValueError, InvalidSignature, KeyError):
        return None
    return claims if isinstance(claims, dict) else None
