"""HTTP to the hub: trust the system's certificate store (a corporate CA used for TLS inspection), `SSL_CERT_FILE` /
`SSL_CERT_DIR` and the public CAs; when the certificate is refused, say why instead of a bare `ConnectError`."""

from __future__ import annotations

import ssl
from typing import Any

import certifi
import httpx

_FIX = (
    "instale a CA da empresa no repositório de certificados do sistema ou aponte SSL_CERT_FILE para o arquivo .pem "
    "dela (nunca desligue a verificação)"
)


def tls() -> ssl.SSLContext:
    """System store (Windows store; OpenSSL paths, which honor SSL_CERT_FILE/SSL_CERT_DIR) plus certifi's public CAs."""
    # ponytail: macOS keychain CAs are not read by OpenSSL; there SSL_CERT_FILE covers it (or `truststore` if asked)
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(certifi.where())
    return ctx


def client(**kw: Any) -> httpx.Client:
    return httpx.Client(verify=tls(), **kw)


def why(e: httpx.HTTPError) -> str:
    """Readable cause: a refused certificate names the reason and the fix; anything else keeps type and message."""
    cause: BaseException | None = e
    while cause is not None:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return f"certificado TLS do hub não confiável ({getattr(cause, 'verify_message', None) or cause}): {_FIX}"
        cause = cause.__cause__ or cause.__context__
    return f"{type(e).__name__}: {e}"
