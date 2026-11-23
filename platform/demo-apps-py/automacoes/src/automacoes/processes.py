"""Processes: a name bound to a package name + SELECTED version, default parameters, timeout and
enabled flag. Changing the version is an admin action with its own audit event."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import asyncpg
from hub_sdk import outbox
from hub_sdk.locale import t
from pydantic import BaseModel, Field

from automacoes.fields import FieldSpec, coerce
from automacoes.model import DEFAULT_TIMEOUT_S, MAX_TIMEOUT_S, PROCESS_COLS, Package, Process, row_to


class ProcessIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    package_name: str = Field(min_length=1)
    package_version: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict, description="Valores padrão dos campos")
    timeout_s: int = Field(default=DEFAULT_TIMEOUT_S, ge=1, le=MAX_TIMEOUT_S)
    enabled: bool = True


def check_defaults(
    fields: Sequence[FieldSpec], params: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Default params: only known fields, each coerced (none is required here — the run form asks)."""
    by_name = {f.name: f for f in fields}
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for k, v in params.items():
        f = by_name.get(k)
        if f is None:
            errors[k] = t("Campo desconhecido neste pacote.")
            continue
        try:
            values[k] = coerce(f.model_copy(update={"required": False}), v)
        except ValueError as e:
            errors[k] = str(e)
    return values, errors


def field_defaults(pkg: Package, p: Process | None) -> dict[str, Any]:
    """Initial form values: the manifest defaults overridden by the process's."""
    base = {f["name"]: f.get("default") for f in pkg.fields}
    return base | (p.params if p is not None else {})


async def list_processes(pool: Any, only_enabled: bool) -> list[Process]:
    async with pool.acquire() as c:
        rows = await c.fetch(
            f"SELECT {PROCESS_COLS} FROM aut_processes WHERE enabled OR NOT $1 ORDER BY name", only_enabled
        )
    return [row_to(Process, r) for r in rows]


async def get_process(pool: Any, pid: int) -> Process | None:
    async with pool.acquire() as c:
        row = await c.fetchrow(f"SELECT {PROCESS_COLS} FROM aut_processes WHERE id = $1", pid)
    return row_to(Process, row) if row is not None else None


class ProcessConflict(ValueError):
    """Name already used, or package version that does not exist."""


async def save_process(conn: Any, old: Process | None, p: ProcessIn, user: str) -> int:
    """Creates (``old`` None) or updates; the audit events go in the caller's transaction."""
    args = (p.name, p.package_name, p.package_version, p.params, p.timeout_s, p.enabled)
    try:
        if old is None:
            pid = await conn.fetchval(
                "INSERT INTO aut_processes (name, package_name, package_version, params, timeout_s, enabled, "
                "created_by) VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id",
                *args,
                user,
            )
        else:
            pid = old.id
            await conn.execute(
                "UPDATE aut_processes SET name = $2, package_name = $3, package_version = $4, params = $5, "
                "timeout_s = $6, enabled = $7 WHERE id = $1",
                pid,
                *args,
            )
    except asyncpg.UniqueViolationError as e:
        raise ProcessConflict(t("Já existe um processo com este nome.")) from e
    except asyncpg.ForeignKeyViolationError as e:
        raise ProcessConflict(t("Esta versão do pacote não existe.")) from e
    await _audit(conn, old, p, int(pid), user)
    return int(pid)


async def _audit(conn: Any, old: Process | None, p: ProcessIn, pid: int, user: str) -> None:
    target = f"processo #{pid} · {p.name}"
    action = "Processo criado" if old is None else "Processo alterado"
    await outbox.write(conn, outbox.audit(user, action, target, f"{p.package_name} {p.package_version}"))
    if old is not None and (old.package_name, old.package_version) != (p.package_name, p.package_version):
        detail = f"{old.package_name} {old.package_version} → {p.package_name} {p.package_version}"
        await outbox.write(conn, outbox.audit(user, "Versão do processo alterada", target, detail))
