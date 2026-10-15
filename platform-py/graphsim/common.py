"""Simulator configuration and the helpers shared by its routes (errors, ids, dates, certificate assertions)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from starlette.requests import Request
from starlette.responses import JSONResponse

CHUNK_UNIT = 320 * 1024
SIMPLE_MAX = 4 * 1024 * 1024


_STR_KEYS = (
    ("tenant", "TENANT"),
    ("client_id", "CLIENT_ID"),
    ("client_secret", "CLIENT_SECRET"),
    ("hostname", "HOSTNAME"),
    ("site_path", "SITE_PATH"),
)
_INT_KEYS = (
    ("token_ttl_s", "TOKEN_TTL_S"),
    ("throttle_every", "THROTTLE_EVERY"),
    ("throttle_retry_after", "THROTTLE_RETRY_AFTER"),
    ("latency_ms", "LATENCY_MS"),
)


def _read_pem(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


@dataclass
class SimConfig:
    tenant: str = "00000000-0000-0000-0000-00000000c0de"
    client_id: str = "11111111-2222-3333-4444-555555555555"
    client_secret: str = "segredo-do-simulador"  # noqa: S105
    cert_pem: str = ""
    hostname: str = "contoso.sharepoint.com"
    site_path: str = "sites/Hub"
    site_name: str = "Hub"
    drives: list[str] = field(default_factory=lambda: ["Documentos", "Arquivos"])
    token_ttl_s: int = 3600
    throttle_every: int = 0
    throttle_next: int = 0
    throttle_retry_after: int = 1
    fail_rate: float = 0.0
    latency_ms: int = 0
    max_bytes: int = 512 * 1024 * 1024
    control: bool = True
    deny_site: bool = False
    link_expiry: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> SimConfig:
        e = os.environ if env is None else env
        c = cls()
        s = lambda k: e.get(k) or None  # noqa: E731
        for attr, key in _STR_KEYS:
            if v := s(f"GRAPHSIM_{key}"):
                setattr(c, attr, v)
        if v := s("GRAPHSIM_CERT_PEM_FILE"):
            c.cert_pem = _read_pem(v)
        if v := s("GRAPHSIM_DRIVES"):
            c.drives = [d.strip() for d in v.split(",")]
        c._apply_numbers(s)
        c.control = s("GRAPHSIM_CONTROL") != "0"
        return c

    def _apply_numbers(self, s: Callable[[str], str | None]) -> None:
        c = self
        for attr, key in _INT_KEYS:
            if (v := s(f"GRAPHSIM_{key}")) and v.isdigit():
                setattr(c, attr, int(v))
        if (v := s("GRAPHSIM_MAX_MB")) and v.isdigit():
            c.max_bytes = int(v) << 20
        if v := s("GRAPHSIM_FAIL_RATE"):
            c.fail_rate = float(v)

    def site_id(self) -> str:
        return f"{self.hostname},6f0d1e2a-0000-4000-8000-000000000001,6f0d1e2a-0000-4000-8000-000000000002"


def drive_id(name: str) -> str:
    return "b!sim_" + re.sub(r"[^A-Za-z0-9]", "_", name)


def rfc3339(t: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if t is None else t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_assertion(jwt: str, pem: str) -> dict[str, Any] | None:
    m = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
    if not m:
        return None
    try:
        cert = x509.load_pem_x509_certificate(m.group().encode())
        der = cert.public_bytes(serialization.Encoding.DER)
        si, sig = jwt.rsplit(".", 1)
        h, c = si.split(".", 1)
        pub = cert.public_key()
        if not isinstance(pub, rsa.RSAPublicKey):
            return None
        pub.verify(_b64u_dec(sig), si.encode(), padding.PKCS1v15(), hashes.SHA256())
        want = base64.urlsafe_b64encode(hashlib.sha1(der).digest()).rstrip(b"=").decode()  # noqa: S324
        if json.loads(_b64u_dec(h)).get("x5t") != want:
            return None
        claims = json.loads(_b64u_dec(c))
    except (ValueError, InvalidSignature, KeyError):
        return None
    return claims if isinstance(claims, dict) else None


def gerr(status: int, code: str, msg: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": msg}}, status_code=status)


def aad(status: int, err: str, desc: str) -> JSONResponse:
    return JSONResponse({"error": err, "error_description": desc}, status_code=status)


NOT_FOUND = ("itemNotFound", "The resource could not be found.")


def base_url(req: Request) -> str:
    return f"http://{req.headers.get('host', 'localhost')}"


def json_dict(body: bytes) -> dict[str, Any] | None:
    try:
        v = json.loads(body)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def move_parent(m: dict[str, dict[str, Any]], v: dict[str, Any], path: str) -> str | None:
    """Destination folder of a PATCH: the referenced folder (None if missing) or the current one."""
    pid = (v.get("parentReference") or {}).get("id")
    if not pid:
        return path.rsplit("/", 1)[0] if "/" in path else ""
    return next((p for p, n in m.items() if n["id"] == pid and n["folder"]), None)


def move_tree(m: dict[str, dict[str, Any]], path: str, dest: str, name: str) -> None:
    for k in [k for k in m if k == path or k.startswith(path + "/")]:
        n = m.pop(k)
        if k == path:
            n.update(name=name, modified=rfc3339(), version=n["version"] + 1)
        m[dest + k[len(path) :]] = n
