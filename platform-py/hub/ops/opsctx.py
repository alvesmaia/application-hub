"""Shared state of the Operations routes (`mount_ops`), handed to each section module."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.templating import Jinja2Templates

from hub.admin.store import AdminStore
from hub.gateway.registry import Registry
from hub.ops import model as m
from hub.ops import runtime as rt
from hub.ops.backups import Backups
from hub.ops.jobs import Jobs
from hub.ops.model import OpsStore
from hub.ops.rollout import Rollout


@dataclass
class OpsCtx:
    app: FastAPI
    templates: Jinja2Templates
    page_ctx: Callable[[Request], dict[str, Any]]
    icon: Callable[[str], str]
    client: httpx.AsyncClient
    data_dir: Path
    admin: AdminStore
    registry: Registry
    store: OpsStore
    eng: rt.Engine
    jobs: Jobs
    backups: Backups
    rollout: Rollout
    api: APIRouter
    name_of: Callable[[str], str]
    spawn: Callable[[Any], None]
    actor_of: Callable[[Request], str]
    log: Callable[[str, str, str, str], None]
    need_app: Callable[[str], Any]
    body: Callable[[Request], Awaitable[dict[str, Any]]]


def tone(s: str) -> str:
    def has(xs: list[str]) -> bool:
        return any(x in s for x in xs)

    if has(["Implantado", "Concluído", "Aprovado", "Em execução", "Válido", "Concluída"]):
        return "status-pill ops-pill-ok"
    if has(["Validando", "Implantando", "Em andamento", "Aguardando", "Na fila", "Cancelando"]):
        return "status-pill ops-pill-warn"
    if has(["Rejeitado", "Falhou", "Parado", "Tentativas esgotadas"]):
        return "status-pill ops-pill-err"
    return "status-pill ops-pill-muted"


def br(v: float, places: int) -> str:
    return f"{v:.{places}f}".replace(".", ",")


def env_key(v: str | None) -> str:
    return "staging" if v == "staging" else "producao"


def rollout_env(label: str) -> str:
    return "staging" if label in (m.STAGING, "staging") else "producao"


def ctx(request: Request) -> OpsCtx:
    """FastAPI dependency: the Operations state that `mount_ops` stored on the app."""
    return cast(OpsCtx, request.app.state.ops_ctx)
