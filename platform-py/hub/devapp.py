"""`hub dev`: o hub mínimo em processo para rodar UM app sozinho dentro do shell real (FAQ 7).

É o próprio `create_app` com um registro de um app só, dados temporários e identidade simulada (sem login),
injetada como chamada em processo (`hub_facade`, a mesma via da API v1/MCP). Os fragmentos vão ao
`upstream` local; só as variáveis de `.hub/dev.env` declaradas em `manifest.env` seguem como `x-hub-env-*`.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from hub.app import create_app
from hub.gateway.proxy import Gateway, GwOptions
from hub.gateway.registry import AppEntry, Registry
from hub.models import AppManifest

DEV_USER = "dev.user"
DEV_NAME = "Desenvolvedor (hub dev)"


def load_env(directory: Path, declared: list[str]) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    """`.hub/dev.env` -> (cabeçalhos, chaves encaminhadas, chaves ignoradas)."""
    path = directory / ".hub/dev.env"
    if not path.is_file():
        return [], [], []
    headers: list[tuple[str, str]] = []
    keys: list[str] = []
    ignored: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        k, sep, v = line.partition("=")
        if not sep:
            raise ValueError(f"linha inválida em {path}: {line}")
        k, v = k.strip(), v.strip()
        if k in declared:
            headers.append((f"x-hub-env-{k.lower()}", v))
            keys.append(k)
        else:
            ignored.append(k)
    return headers, keys, ignored


def load_app(directory: Path, upstream: str) -> tuple[AppEntry, list[tuple[str, str]], list[str], list[str]]:
    mf = directory / "manifest.json"
    if not mf.is_file():
        raise ValueError(f"manifest.json não encontrado em {directory}")
    raw: dict[str, Any] = json.loads(mf.read_text(encoding="utf-8"))
    manifest = AppManifest.model_validate(raw)
    headers, keys, ignored = load_env(directory, [str(e) for e in raw.get("env", [])])
    entry = AppEntry(
        id=manifest.id,
        name=manifest.name or manifest.id,
        upstream=upstream.rstrip("/"),
        version=str(manifest.version or ""),
        manifest=manifest,
    )
    return entry, headers, keys, ignored


class _DevIdentity:
    """ASGI: toda requisição chega como o usuário simulado (já autorizado, como chamada em processo)."""

    def __init__(self, inner: ASGIApp, role: str) -> None:
        self.inner = inner
        self.user = {
            "id": DEV_USER,
            "email": DEV_USER,
            "name": DEV_NAME,
            "roles": [role],
            "active": True,
            "must_change_password": False,
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            scope = {**scope, "hub_facade": (self.user, None)}
        await self.inner(scope, receive, send)


def dev_asgi(directory: Path, upstream: str, role: str = "admin") -> tuple[ASGIApp, AppEntry]:
    entry, headers, _, _ = load_app(directory, upstream)
    os.environ.setdefault("HUB_DATA_DIR", tempfile.mkdtemp(prefix="hub-dev-"))
    os.environ.setdefault("HUB_BROKER_ADDR", "off")
    gw = Gateway(Registry([entry]), GwOptions())
    app = create_app(gateway=gw)
    gw.extra_headers = lambda _request, _app_id, _ident: list(headers)
    name = "Administrador" if role.lower() in ("admin", "administrador") else role
    return _DevIdentity(app, name), entry
