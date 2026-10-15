"""Validation of a connection's input (create/update): common fields, then the kind-specific ones."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from hub.connections.conn_model import (
    KINDS,
    MAX_LINK_HOURS,
    ConnError,
    _text,
    apply_cert,
    make_backend,
    now_ms,
    valid_name,
)
from hub.connections.sql import DEFAULT_PORT, MAX_ROWS_CAP, MAX_TIMEOUT_S, SQL_KINDS
from hub.storage import OPS, StorageError, clean
from hub.storage.graph import valid_guid, valid_site, valid_tenant


def build(
    c: dict[str, Any],
    i: dict[str, Any],
    creating: bool,
    apps: list[str],
    check_bindings: Callable[[Any], list[dict[str, str]]],
) -> dict[str, Any]:
    _identity(c, i, creating)
    _common(c, i, apps, check_bindings)
    secret = str(i.get("secret", "")).strip()
    if len(secret) > 2048 or any(ord(ch) < 32 or ord(ch) == 127 for ch in secret):
        raise ConnError("Segredo inválido.")
    if c["kind"] in SQL_KINDS:
        _sql(c, i, secret, creating)
        return c
    if c["kind"] == "sharepoint":
        _sharepoint(c, i, secret, creating)
        _anonymous_links(c)
    else:
        _s3(c, i, secret, creating)
    try:
        make_backend(c)  # validates endpoint/bucket without network access
    except StorageError as e:
        raise ConnError(e.message) from e
    return c


def _identity(c: dict[str, Any], i: dict[str, Any], creating: bool) -> None:
    if creating:
        name = str(i.get("name", "")).strip()
        if not valid_name(name):
            raise ConnError(
                "Nome: 2 a 40 caracteres, letras minúsculas, números e hífen (ex.: contratos-docs)."
            )
        if i.get("kind") not in KINDS:
            raise ConnError("Escolha o tipo da conexão.")
        c["name"], c["kind"] = name, i["kind"]
    elif i.get("kind") and i["kind"] != c["kind"]:
        raise ConnError("O tipo da conexão não muda depois de criada.")


def _common(
    c: dict[str, Any],
    i: dict[str, Any],
    apps: list[str],
    check_bindings: Callable[[Any], list[dict[str, str]]],
) -> None:
    c["description"] = _text(i.get("description"), 200, "Descrição")
    try:
        c["base_folder"] = clean(str(i.get("base_folder", "")))
    except StorageError as e:
        raise ConnError(f"Pasta-base: {e.message}") from e
    if (ops := i.get("allowed_ops")) is not None:
        c["allowed_ops"] = [o for o in OPS if o in ops]
        if not c["allowed_ops"]:
            raise ConnError("Libere ao menos uma operação.")
    if (mb := i.get("max_upload_mb")) is not None:
        if not isinstance(mb, int) or not 1 <= mb <= 2048:
            raise ConnError("Tamanho máximo por arquivo: de 1 a 2048 MB.")
        c["max_upload_mb"] = mb
    if (b := i.get("bindings")) is not None:
        c["bindings"] = check_bindings(b)
    _links(c, i, apps)


def _links(c: dict[str, Any], i: dict[str, Any], apps: list[str]) -> None:
    if (h := i.get("max_link_hours")) is not None:
        if not isinstance(h, int) or not 1 <= h <= MAX_LINK_HOURS:
            raise ConnError(f"Validade máxima dos links: de 1 a {MAX_LINK_HOURS} horas.")
        c["max_link_hours"] = h
    if (e := i.get("allow_edit_links")) is not None:
        c["allow_edit_links"] = bool(e)
    if (sel := i.get("anonymous_link_apps")) is not None:
        for a in sel:
            if a not in apps:
                raise ConnError(f"Aplicação desconhecida: {a}.")
        c["anonymous_link_apps"] = sorted(set(sel))


def _anonymous_links(c: dict[str, Any]) -> None:
    if c["share_scope"] != "anonymous":
        return
    if not c["anonymous_link_apps"]:
        raise ConnError(
            "Links anônimos: escolha quais apps podem gerá-los (os demais usam o alcance da organização)."
        )
    if c["allow_edit_links"]:
        raise ConnError(
            'Links anônimos não podem ser de edição: desmarque "links de edição" ou use outro alcance.'
        )


def _sharepoint(c: dict[str, Any], i: dict[str, Any], secret: str, creating: bool) -> None:
    c["tenant_id"] = str(i.get("tenant_id", "")).strip()
    c["client_id"] = str(i.get("client_id", "")).strip().lower()
    c["site"] = str(i.get("site", "")).strip().rstrip("/")
    c["drive"] = _text(i.get("drive"), 120, "Biblioteca")
    if not valid_tenant(c["tenant_id"]):
        raise ConnError("Tenant ID: o GUID do diretório ou o domínio (ex.: contoso.onmicrosoft.com).")
    if not valid_guid(c["client_id"]):
        raise ConnError("Client ID: o GUID do app registration (Application ID).")
    if not valid_site(c["site"]):
        raise ConnError("Site: a URL https://<empresa>.sharepoint.com/sites/<site> ou o id do site.")
    scope = str(i.get("share_scope", "")).strip()
    if scope not in ("", "organization", "users", "anonymous"):
        raise ConnError("Alcance do link: organization, users ou anonymous.")
    c["share_scope"] = scope or "organization"
    _sharepoint_auth(c, i, secret, creating)


def _sharepoint_auth(c: dict[str, Any], i: dict[str, Any], secret: str, creating: bool) -> None:
    auth = i.get("auth") or c["auth"]
    if auth not in ("secret", "certificate"):
        raise ConnError("Autenticação: secret ou certificate.")
    switching = creating or auth != c["auth"]
    if auth == "certificate":
        if i.get("certificate_b64"):
            apply_cert(c, str(i["certificate_b64"]), str(i.get("certificate_password", "")))
        elif switching:
            raise ConnError("Envie o certificado (PEM com a chave, ou PFX com a senha).")
        c["secret"] = ""
    else:
        if not secret and switching:
            raise ConnError("Informe o segredo do cliente (client secret).")
        if secret:
            c["secret"], c["credential_updated_at"] = secret, now_ms()
        c.update(certificate="", cert_thumbprint="", cert_subject="", cert_not_after=None)
    c["auth"] = auth


def _sql_target(c: dict[str, Any], i: dict[str, Any]) -> dict[str, Any]:
    host = str(i.get("host", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,252})", host):
        raise ConnError("Servidor: nome ou IP do banco (ex.: db.empresa.local).")
    port = i.get("port") or DEFAULT_PORT[c["kind"]]
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConnError("Porta: de 1 a 65535.")
    database = _text(i.get("database"), 128, "Banco")
    username = _text(i.get("username"), 128, "Usuário")
    if not database or not username:
        raise ConnError("Informe o banco e o usuário.")
    ssl = str(i.get("ssl") or c["ssl"] or "prefer")
    if ssl not in ("disable", "prefer", "require"):
        raise ConnError("TLS: disable, prefer ou require.")
    return {"host": host, "port": port, "database": database, "username": username, "ssl": ssl}


def _sql_limits(c: dict[str, Any], i: dict[str, Any]) -> dict[str, Any]:
    mr = i.get("max_rows", c["max_rows"])
    if not isinstance(mr, int) or not 1 <= mr <= MAX_ROWS_CAP:
        raise ConnError(f"Máximo de linhas por consulta: de 1 a {MAX_ROWS_CAP}.")
    ts = i.get("timeout_s", c["timeout_s"])
    if not isinstance(ts, int) or not 1 <= ts <= MAX_TIMEOUT_S:
        raise ConnError(f"Tempo máximo por consulta: de 1 a {MAX_TIMEOUT_S} s.")
    return {"max_rows": mr, "timeout_s": ts}


def _sql(c: dict[str, Any], i: dict[str, Any], secret: str, creating: bool) -> None:
    target = _sql_target(c, i)
    limits = _sql_limits(c, i)
    if not secret and creating:
        raise ConnError("Informe a senha do usuário do banco.")
    if secret:
        c["secret"], c["credential_updated_at"] = secret, now_ms()
    c.update(
        **target,
        readonly=bool(i.get("readonly", c["readonly"])),
        **limits,
        allowed_ops=["query"],
        auth="secret",
        share_scope="",
        allow_edit_links=False,
        anonymous_link_apps=[],
    )


def _s3(c: dict[str, Any], i: dict[str, Any], secret: str, creating: bool) -> None:
    c["endpoint"] = str(i.get("endpoint", "")).strip().rstrip("/")
    c["bucket"] = str(i.get("bucket", "")).strip()
    c["region"] = _text(i.get("region"), 40, "Região")
    c["access_key"] = _text(i.get("access_key"), 128, "Access key")
    c["public_url"] = str(i.get("public_url", "")).strip().rstrip("/")
    if not c["access_key"]:
        raise ConnError("Informe a access key.")
    if not secret and creating:
        raise ConnError("Informe a secret key.")
    if secret:
        c["secret"], c["credential_updated_at"] = secret, now_ms()
    if c["public_url"] and not c["public_url"].startswith(("http://", "https://")):
        raise ConnError("URL pública: http(s)://…")
    # pre-signed URL = read-only.
    c.update(auth="secret", share_scope="", allow_edit_links=False)
