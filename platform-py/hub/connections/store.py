"""Storage connections (SharePoint/Graph and S3): `connections.json` with secrets in the vault, links and
managed broker variables (`HUB_CONNECTIONS_*`, `HUB_STORAGE_TOKEN_*`, `HUB_STORAGE_URL`)."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from hub.admin.config import APP_ENVS, env_suffix
from hub.admin.store import AdminStore
from hub.connections import conn_build
from hub.connections.conn_model import (
    CONNECTIONS_ENV,
    FILE,
    MAX_CONNECTIONS,
    STORAGE_TOKEN_ENV,
    STORAGE_URL_ENV,
    apply_cert,
    blank,
    make_backend,
    now_ms,
)
from hub.connections.conn_model import Backend as Backend
from hub.connections.conn_model import ConnError as ConnError
from hub.connections.conn_model import bindings_text as bindings_text
from hub.connections.conn_model import link_policy as link_policy
from hub.connections.conn_model import target as target
from hub.connections.conn_model import valid_name as valid_name
from hub.connections.conn_model import view as view
from hub.connections.sql import SqlBackend
from hub.state import StateDb
from hub.vault import Vault, is_sealed


class ConnStore:
    def __init__(
        self, data_dir: Path, vault: Vault | None, admin: AdminStore, db: StateDb | None = None
    ) -> None:
        self.path, self.vault, self.admin, self.db = data_dir / FILE, vault, admin, db
        self.lock = asyncio.Lock()
        self.clients: dict[str, tuple[int, Backend]] = {}
        self._closing: set[asyncio.Task[None]] = set()
        self.items: list[dict[str, Any]] = []
        self.storage: list[dict[str, Any]] = []  # provisioned storage (bucket + user + connection)
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

    # ---- validation ------------------------------------------------------------------------
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
        return conn_build.build(c, i, creating, self.apps, self.check_bindings)

    # ---- operations ------------------------------------------------------------------------
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

    # ---- broker managed variables -----------------------------------------------------------
    async def sync_env(self, actor: str, broker_url: str) -> None:
        """Reflects the links in the managed variables (restart pending, as with ordinary variables)."""
        cfg = self.admin.cfg
        bound: dict[tuple[str, str], list[str]] = {}
        for c in self.items:
            for b in c["bindings"]:
                bound.setdefault((b["app"], b["env"]), []).append(f"{c['name']}={c['kind']}")
        existing = {v["name"] for v in await cfg.env_list()}
        for app in self.apps:
            for env, _ in APP_ENVS:
                await self._sync_pair(actor, app, env, bound.get((app, env)), existing)
        urls = [{"app": a, "env": e} for a, e in sorted(bound)]
        if urls:
            await cfg.upsert_managed(
                actor,
                {"name": STORAGE_URL_ENV, "value": broker_url, "kind": "publica", "bindings": urls},
                "Endereço do broker de armazenamento",
            )
        elif STORAGE_URL_ENV in existing:
            await cfg.env_delete(actor, STORAGE_URL_ENV)

    async def _sync_pair(
        self, actor: str, app: str, env: str, names: list[str] | None, existing: set[str]
    ) -> None:
        """Connections list + broker token of one app × environment; both removed when nothing is bound."""
        cfg = self.admin.cfg
        suffix = f"{env_suffix(app)}_{env_suffix(env)}"
        list_name, tok_name = f"{CONNECTIONS_ENV}_{suffix}", f"{STORAGE_TOKEN_ENV}_{suffix}"
        pair = {"app": app, "env": env}
        if names is None:
            for n in (list_name, tok_name):
                if n in existing:
                    await cfg.env_delete(actor, n)
            return
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

    async def token_owner(self, token: str) -> tuple[str, str] | None:
        """(app, environment) that owns the broker token: only the token MANAGED and linked exactly to that pair counts;
        scans all pairs without exiting early (constant time)."""
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


__all__ = ["ConnError", "ConnStore", "bindings_text", "link_policy", "target", "valid_name", "view"]
