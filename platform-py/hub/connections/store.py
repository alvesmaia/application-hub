"""Conexões de armazenamento (SharePoint/Graph e S3): `connections.json` com segredos no cofre, vínculos e
variáveis geridas do broker (`HUB_CONNECTIONS_*`, `HUB_STORAGE_TOKEN_*`, `HUB_STORAGE_URL`)."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import re
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from hub.admin.config import APP_ENVS, env_label, env_suffix
from hub.admin.store import AdminStore
from hub.connections.sql import DEFAULT_PORT, MAX_ROWS_CAP, MAX_TIMEOUT_S, SQL_KINDS, SqlBackend, SqlConfig
from hub.state import StateDb
from hub.storage import OPS, StorageError, clean
from hub.storage.cert import ClientCert
from hub.storage.graph import Endpoints, GraphClient, SharePointConfig, valid_guid, valid_site, valid_tenant
from hub.storage.s3 import S3Client, S3Config
from hub.vault import Vault, is_sealed

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
    """Visão da tela e da API (campos e ordem do `ConnView` da versão anterior do hub): sem segredo nem certificado."""
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
    """Alcance efetivo do link de compartilhamento para `app` ou a recusa (pt-BR)."""
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


class ConnStore:
    def __init__(
        self, data_dir: Path, vault: Vault | None, admin: AdminStore, db: StateDb | None = None
    ) -> None:
        self.path, self.vault, self.admin, self.db = data_dir / FILE, vault, admin, db
        self.lock = asyncio.Lock()
        self.clients: dict[str, tuple[int, Backend]] = {}
        self._closing: set[asyncio.Task[None]] = set()
        self.items: list[dict[str, Any]] = []
        self.storage: list[dict[str, Any]] = []  # armazenamento provisionado (bucket + usuário + conexão)
        self._load()

    @property
    def apps(self) -> list[str]:
        return self.admin.apps

    def _aad(self, c: dict[str, Any], field: str) -> str:
        return f"connection:{c['name']}:{field}"

    def _load(self) -> None:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        raw = doc.get("connections", [])
        self.storage = doc.get("storage", [])
        for c in raw:
            for field in ("secret", "certificate"):
                if c.get(field) and is_sealed(c[field]):
                    if not self.vault:
                        raise ValueError(f"{self.path}: credenciais cifradas, mas o cofre não foi aberto")
                    c[field] = self.vault.open_value(c[field], self._aad(c, field))
            c.update({**blank(c.get("created_at", 0)), **c})
        self.items = list(raw)

    def _persist(self) -> None:
        disk = json.loads(json.dumps(self.items))
        for c in disk:
            for field in ("secret", "certificate"):
                if c[field] and self.vault:
                    c[field] = self.vault.seal(c[field], self._aad(c, field))
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"connections": disk, "storage": self.storage}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with suppress(OSError):
            tmp.chmod(0o600)
        os.replace(tmp, self.path)

    @asynccontextmanager
    async def mutating(self) -> AsyncIterator[list[dict[str, Any]]]:
        async with self.lock:
            if self.db:
                async with self.db.file_lock(FILE, write=True):
                    self._load()
                    nxt = json.loads(json.dumps(self.items))
                    yield nxt
                    self.items = nxt
                    self._persist()
            else:
                nxt = json.loads(json.dumps(self.items))
                yield nxt
                self.items = nxt
                self._persist()

    async def storage_update(self, fn: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> None:
        async with self.mutating():
            self.storage = fn(list(self.storage))

    def all(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self.items]

    def get(self, name: str) -> dict[str, Any] | None:
        return next((dict(c) for c in self.items if c["name"] == name), None)

    # ---- backend ---------------------------------------------------------------------------
    def backend(self, c: dict[str, Any], transport: Any = None) -> Backend:
        cached = self.clients.get(c["name"])
        if cached and cached[0] == c["rev"]:
            return cached[1]
        if cached and isinstance(cached[1], SqlBackend):  # credencial/servidor mudou: fecha o pool antigo
            with suppress(RuntimeError):
                task = asyncio.get_running_loop().create_task(cached[1].close())
                self._closing.add(task)
                task.add_done_callback(self._closing.discard)
        b = make_backend(c, transport)
        self.clients[c["name"]] = (c["rev"], b)
        return b

    # ---- validação -------------------------------------------------------------------------
    def check_bindings(self, bindings: Any) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for b in bindings:
            app, env = str(b.get("app", "")), str(b.get("env", ""))
            if app not in self.apps:
                raise ConnError(f"Aplicação desconhecida: {app}.")
            if env not in dict(APP_ENVS):
                raise ConnError("Ambiente inválido (use staging ou producao).")
            if {"app": app, "env": env} not in out:
                out.append({"app": app, "env": env})
        return sorted(out, key=lambda b: (b["app"], b["env"]))

    def build(self, c: dict[str, Any], i: dict[str, Any], creating: bool) -> dict[str, Any]:
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
            c["bindings"] = self.check_bindings(b)
        if (h := i.get("max_link_hours")) is not None:
            if not isinstance(h, int) or not 1 <= h <= MAX_LINK_HOURS:
                raise ConnError(f"Validade máxima dos links: de 1 a {MAX_LINK_HOURS} horas.")
            c["max_link_hours"] = h
        if (e := i.get("allow_edit_links")) is not None:
            c["allow_edit_links"] = bool(e)
        if (apps := i.get("anonymous_link_apps")) is not None:
            for a in apps:
                if a not in self.apps:
                    raise ConnError(f"Aplicação desconhecida: {a}.")
            c["anonymous_link_apps"] = sorted(set(apps))
        secret = str(i.get("secret", "")).strip()
        if len(secret) > 2048 or any(ord(ch) < 32 or ord(ch) == 127 for ch in secret):
            raise ConnError("Segredo inválido.")
        if c["kind"] in SQL_KINDS:
            self._sql(c, i, secret, creating)
            return c
        if c["kind"] == "sharepoint":
            self._sharepoint(c, i, secret, creating)
        else:
            self._s3(c, i, secret, creating)
        if c["kind"] == "sharepoint" and c["share_scope"] == "anonymous":
            if not c["anonymous_link_apps"]:
                raise ConnError(
                    "Links anônimos: escolha quais apps podem gerá-los (os demais usam o alcance da organização)."
                )
            if c["allow_edit_links"]:
                raise ConnError(
                    'Links anônimos não podem ser de edição: desmarque "links de edição" ou use outro alcance.'
                )
        try:
            make_backend(c)  # valida endpoint/bucket sem rede
        except StorageError as e:
            raise ConnError(e.message) from e
        return c

    @staticmethod
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

    @staticmethod
    def _sql(c: dict[str, Any], i: dict[str, Any], secret: str, creating: bool) -> None:
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
        mr = i.get("max_rows", c["max_rows"])
        if not isinstance(mr, int) or not 1 <= mr <= MAX_ROWS_CAP:
            raise ConnError(f"Máximo de linhas por consulta: de 1 a {MAX_ROWS_CAP}.")
        ts = i.get("timeout_s", c["timeout_s"])
        if not isinstance(ts, int) or not 1 <= ts <= MAX_TIMEOUT_S:
            raise ConnError(f"Tempo máximo por consulta: de 1 a {MAX_TIMEOUT_S} s.")
        if not secret and creating:
            raise ConnError("Informe a senha do usuário do banco.")
        if secret:
            c["secret"], c["credential_updated_at"] = secret, now_ms()
        c.update(
            host=host,
            port=port,
            database=database,
            username=username,
            ssl=ssl,
            readonly=bool(i.get("readonly", c["readonly"])),
            max_rows=mr,
            timeout_s=ts,
            allowed_ops=["query"],
            auth="secret",
            share_scope="",
            allow_edit_links=False,
            anonymous_link_apps=[],
        )

    @staticmethod
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
        # URL pré-assinada = só leitura.
        c.update(auth="secret", share_scope="", allow_edit_links=False)

    # ---- operações -------------------------------------------------------------------------
    async def create(self, i: dict[str, Any]) -> dict[str, Any]:
        c = self.build(blank(now_ms()), i, True)
        async with self.mutating() as items:
            if any(x["name"] == c["name"] for x in items):
                raise ConnError("Já existe uma conexão com esse nome.")
            if len(items) >= MAX_CONNECTIONS:
                raise ConnError("Limite de conexões atingido.")
            items.append(c)
            items.sort(key=lambda x: x["name"])
        return c

    async def update(self, name: str, i: dict[str, Any]) -> dict[str, Any]:
        async with self.mutating() as items:
            slot = next((x for x in items if x["name"] == name), None)
            if slot is None:
                raise ConnError("Conexão não encontrada.", 404)
            c = self.build(dict(slot), i, False)
            c.update(updated_at=now_ms(), rev=c["rev"] + 1, last_test=None)
            items[items.index(slot)] = c
        return c

    async def set_bindings(self, name: str, bindings: Any) -> dict[str, Any]:
        b = self.check_bindings(bindings)
        async with self.mutating() as items:
            slot = next((x for x in items if x["name"] == name), None)
            if slot is None:
                raise ConnError("Conexão não encontrada.", 404)
            slot.update(bindings=b, updated_at=now_ms())
            return dict(slot)

    async def delete(self, name: str) -> dict[str, Any]:
        async with self.mutating() as items:
            slot = next((x for x in items if x["name"] == name), None)
            if slot is None:
                raise ConnError("Conexão não encontrada.", 404)
            items.remove(slot)
        self.clients.pop(name, None)
        return slot

    async def rotate(self, name: str, i: dict[str, Any]) -> dict[str, Any]:
        secret = str(i.get("secret", "")).strip()
        async with self.mutating() as items:
            slot = next((x for x in items if x["name"] == name), None)
            if slot is None:
                raise ConnError("Conexão não encontrada.", 404)
            if slot["kind"] == "sharepoint" and slot["auth"] == "certificate":
                if not i.get("certificate_b64"):
                    raise ConnError("Envie o novo certificado.")
                apply_cert(slot, str(i["certificate_b64"]), str(i.get("certificate_password", "")))
            elif not secret or len(secret) > 2048 or any(ord(ch) < 32 or ord(ch) == 127 for ch in secret):
                raise ConnError("Informe o novo segredo.")
            else:
                slot["secret"], slot["credential_updated_at"] = secret, now_ms()
            slot.update(updated_at=now_ms(), last_test=None)
            slot["rev"] += 1
            return dict(slot)

    async def set_last_test(self, name: str, rev: int, last: dict[str, Any]) -> None:
        async with self.mutating() as items:
            for x in items:
                if x["name"] == name and x["rev"] == rev:
                    x["last_test"] = last

    # ---- variáveis geridas do broker --------------------------------------------------------
    async def sync_env(self, actor: str, broker_url: str) -> None:
        """Reflete os vínculos nas variáveis geridas (reinício pendente como as variáveis comuns)."""
        cfg = self.admin.cfg
        bound: dict[tuple[str, str], list[str]] = {}
        for c in self.items:
            for b in c["bindings"]:
                bound.setdefault((b["app"], b["env"]), []).append(f"{c['name']}={c['kind']}")
        existing = {v["name"] for v in await cfg.env_list()}
        for app in self.apps:
            for env, _ in APP_ENVS:
                suffix = f"{env_suffix(app)}_{env_suffix(env)}"
                list_name, tok_name = f"{CONNECTIONS_ENV}_{suffix}", f"{STORAGE_TOKEN_ENV}_{suffix}"
                pair = {"app": app, "env": env}
                if (names := bound.get((app, env))) is not None:
                    await cfg.upsert_managed(
                        actor,
                        {
                            "name": list_name,
                            "value": ",".join(sorted(names)),
                            "kind": "publica",
                            "bindings": [pair],
                            "inject_as": CONNECTIONS_ENV,
                        },
                        "Conexões do app atualizadas",
                    )
                    if await cfg.managed_var(tok_name) is None:
                        await cfg.upsert_managed(
                            actor,
                            {
                                "name": tok_name,
                                "value": secrets.token_hex(32),
                                "kind": "secreta",
                                "bindings": [pair],
                                "inject_as": STORAGE_TOKEN_ENV,
                            },
                            "Token do broker de armazenamento gerado",
                        )
                else:
                    for n in (list_name, tok_name):
                        if n in existing:
                            await cfg.env_delete(actor, n)
        urls = [{"app": a, "env": e} for a, e in sorted(bound)]
        if urls:
            await cfg.upsert_managed(
                actor,
                {"name": STORAGE_URL_ENV, "value": broker_url, "kind": "publica", "bindings": urls},
                "Endereço do broker de armazenamento",
            )
        elif STORAGE_URL_ENV in existing:
            await cfg.env_delete(actor, STORAGE_URL_ENV)

    async def token_owner(self, token: str) -> tuple[str, str] | None:
        """(app, ambiente) dono do token do broker: só o token GERIDO e vinculado exatamente a esse par vale;
        varre todos os pares sem sair cedo (tempo constante)."""
        if len(token) < 16:
            return None
        env = {v["name"]: v for v in (await self.admin.cfg.read())["env"]}
        found: tuple[str, str] | None = None
        for app in self.apps:
            for e, _ in APP_ENVS:
                v = env.get(f"{STORAGE_TOKEN_ENV}_{env_suffix(app)}_{env_suffix(e)}")
                if not v:
                    continue
                exact = (
                    v.get("managed")
                    and v.get("inject_as") == STORAGE_TOKEN_ENV
                    and v.get("bindings") == [{"app": app, "env": e}]
                )
                if hmac.compare_digest(v["value"].encode(), token.encode()) and exact:
                    found = (str(app), str(e))
        return found

    async def rotate_token(self, actor: str, app: str, env: str) -> None:
        name = f"{STORAGE_TOKEN_ENV}_{env_suffix(app)}_{env_suffix(env)}"
        if await self.admin.cfg.managed_var(name) is None:
            raise ConnError("Este app não tem token do broker nesse ambiente (vincule uma conexão).", 404)
        await self.admin.cfg.rotate_managed(
            actor, name, secrets.token_hex(32), "Token do broker de armazenamento rotacionado"
        )


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


__all__ = ["ConnError", "ConnStore", "bindings_text", "link_policy", "target", "valid_name", "view"]
