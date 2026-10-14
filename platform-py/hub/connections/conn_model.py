"""Connection records: limits, defaults, the screen/API view, link policy, certificates and backend factory."""

from __future__ import annotations

import base64
import re
import time
from typing import Any

from hub.admin.config import env_label
from hub.connections.sql import SQL_KINDS, SqlBackend, SqlConfig
from hub.storage import StorageError
from hub.storage.cert import ClientCert
from hub.storage.graph import Endpoints, GraphClient, SharePointConfig
from hub.storage.s3 import S3Client, S3Config

FILE = "connections.json"
MAX_CONNECTIONS = 100
MAX_LINK_HOURS = 720
STORAGE_TOKEN_ENV, CONNECTIONS_ENV, STORAGE_URL_ENV = (
    "HUB_STORAGE_TOKEN",
    "HUB_CONNECTIONS",
    "HUB_STORAGE_URL",
)
MASK = "••••••••"
Backend = GraphClient | S3Client | SqlBackend
KINDS = ("sharepoint", "s3", *SQL_KINDS)


class ConnError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def valid_name(n: str) -> bool:
    return re.fullmatch(r"[a-z][a-z0-9-]{1,39}", n) is not None and not n.endswith("-")


def _text(s: Any, mx: int, what: str) -> str:
    t = str(s or "").strip()
    if len(t) > mx or any(ord(c) < 32 or ord(c) == 127 for c in t):
        raise ConnError(f"{what}: até {mx} caracteres, sem quebras de linha.")
    return t


def blank(now: int) -> dict[str, Any]:
    return {
        "name": "",
        "kind": "sharepoint",
        "description": "",
        "tenant_id": "",
        "client_id": "",
        "site": "",
        "drive": "",
        "auth": "secret",
        "share_scope": "organization",
        "allow_edit_links": False,
        "anonymous_link_apps": [],
        "max_link_hours": 168,
        "endpoint": "",
        "bucket": "",
        "region": "",
        "access_key": "",
        "public_url": "",
        "base_folder": "",
        "allowed_ops": ["upload", "download", "list", "delete", "move"],
        "max_upload_mb": 100,
        "bindings": [],
        "secret": "",
        "certificate": "",
        "cert_thumbprint": "",
        "cert_not_after": None,
        "cert_subject": "",
        "created_at": now,
        "updated_at": now,
        "credential_updated_at": now,
        "last_test": None,
        "rev": 1,
        "host": "",
        "port": 0,
        "database": "",
        "username": "",
        "ssl": "prefer",
        "readonly": True,
        "max_rows": 5000,
        "timeout_s": 30,
    }


_VIEW = [
    "name",
    "kind",
    "description",
    "tenant_id",
    "client_id",
    "site",
    "drive",
    "auth",
    "share_scope",
    "allow_edit_links",
    "anonymous_link_apps",
    "max_link_hours",
    "endpoint",
    "bucket",
    "region",
    "access_key",
    "public_url",
    "base_folder",
    "allowed_ops",
    "max_upload_mb",
    "bindings",
    "secret",
    "cert_thumbprint",
    "cert_not_after",
    "cert_subject",
    "created_at",
    "updated_at",
    "credential_updated_at",
    "last_test",
    "host",
    "port",
    "database",
    "username",
    "ssl",
    "readonly",
    "max_rows",
    "timeout_s",
]


def view(c: dict[str, Any]) -> dict[str, Any]:
    """View for the screen and the API (fields and order of the `ConnView` of the previous hub version): no secret or certificate."""
    out = {k: c[k] for k in _VIEW}
    out["secret"] = MASK if c["secret"] or c["certificate"] else ""
    return out


def target(c: dict[str, Any]) -> str:
    base = f" / {c['base_folder']}" if c["base_folder"] else ""
    if c["kind"] in SQL_KINDS:
        return f"{c['host']}:{c['port']} · {c['database']}"
    if c["kind"] == "sharepoint":
        return f"{c['site']} · {c['drive'] or '(biblioteca padrão)'}{base}"
    return f"{c['endpoint']} · {c['bucket']}{base}"


def link_policy(c: dict[str, Any], app: str, kind: str, hours: int) -> str:
    """Effective reach of the sharing link for `app`, or the refusal (pt-BR)."""
    if kind not in ("view", "edit"):
        raise ConnError("Tipo de link: view ou edit.")
    mx = min(max(c["max_link_hours"], 1), MAX_LINK_HOURS)
    if not 1 <= hours <= mx:
        raise ConnError(f"Validade do link: de 1 a {mx} horas nesta conexão.")
    anon_ok = app in c["anonymous_link_apps"]
    if c["kind"] == "s3":
        if not anon_ok:
            raise ConnError(
                "Links pré-assinados (qualquer pessoa com o link) não liberados para este app nesta conexão."
            )
        scope = "anonymous"
    else:
        scope = (
            "anonymous"
            if c["share_scope"] == "anonymous" and anon_ok
            else "users"
            if c["share_scope"] == "users"
            else "organization"
        )
    if kind == "edit" and (not c["allow_edit_links"] or scope == "anonymous"):
        raise ConnError("Links de edição não liberados nesta conexão.")
    return scope


def apply_cert(c: dict[str, Any], b64: str, password: str) -> None:
    try:
        raw = base64.b64decode(b64.strip(), validate=False)
    except ValueError as e:
        raise ConnError("Certificado: arquivo ilegível.") from e
    if len(raw) > 64 * 1024:
        raise ConnError("Certificado: arquivo grande demais (até 64 KB).")
    try:
        cert = ClientCert.parse(raw, password)
    except StorageError as e:
        raise ConnError(e.message) from e
    if cert.not_after is not None and cert.not_after < time.time():
        raise ConnError("Certificado vencido.")
    c.update(
        certificate=cert.to_stored(),
        cert_thumbprint=cert.thumbprint,
        cert_subject=cert.subject,
        cert_not_after=cert.not_after,
        credential_updated_at=now_ms(),
    )


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def make_backend(c: dict[str, Any], transport: Any = None) -> Backend:
    if c["kind"] in SQL_KINDS:
        return SqlBackend(
            SqlConfig(
                c["kind"],
                c["host"],
                int(c["port"]),
                c["database"],
                c["username"],
                c["secret"],
                c["ssl"],
                bool(c["readonly"]),
                int(c["max_rows"]),
                int(c["timeout_s"]),
            )
        )
    if c["kind"] == "sharepoint":
        cert = (
            ClientCert.from_stored(c["certificate"])
            if c["auth"] == "certificate" and c["certificate"]
            else None
        )
        cfg = SharePointConfig(
            c["tenant_id"], c["client_id"], c["secret"], c["site"], c["drive"], c["base_folder"], cert
        )
        return GraphClient(Endpoints.from_env(), cfg, transport)
    return S3Client(
        S3Config(
            c["endpoint"],
            c["bucket"],
            c["access_key"],
            c["secret"],
            c["region"] or "us-east-1",
            c["base_folder"],
            c["public_url"],
        ),
        transport,
    )


def bindings_text(b: list[dict[str, str]]) -> str:
    return ", ".join(f"{x['app']} ({env_label(x['env'])})" for x in b) or "sem vínculos"
