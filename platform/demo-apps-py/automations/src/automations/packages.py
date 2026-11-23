"""Packages in Postgres: register an uploaded zip (checked by :mod:`automations.package_format`;
versions are immutable — the same name+version twice is refused), list, versions, content."""

from __future__ import annotations

from typing import Any

from hub_sdk import outbox

from automations.model import PACKAGE_COLS, Package, row_to
from automations.package_format import PackageError, inspect_zip


class DuplicateVersion(ValueError):
    """Same name and version already stored (versions are immutable)."""


async def register(conn: Any, data: bytes, user: str) -> Package:
    """Stores the package (audit in the caller's transaction). Raises :class:`PackageError`
    (invalid, with every problem) or :class:`DuplicateVersion`."""
    ins = inspect_zip(data)
    m = ins.manifest
    if m is None:
        raise PackageError(ins.errors)
    row = await conn.fetchrow(
        "INSERT INTO aut_packages (name, version, description, entrypoint, fields, labels, sha256, size, content, "
        "uploaded_by) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) "
        f"ON CONFLICT (name, version) DO NOTHING RETURNING {PACKAGE_COLS}",
        m.name, m.version, m.description, m.entrypoint, [f.model_dump(mode="json") for f in m.fields], m.labels,
        ins.sha256, ins.size, data, user,
    )  # fmt: skip
    if row is None:
        raise DuplicateVersion(f"{m.name} {m.version}: esta versão já foi enviada (versões são imutáveis)")
    await outbox.write(
        conn, outbox.audit(user, "Pacote enviado", f"{m.name} {m.version}", f"sha256 {ins.sha256}")
    )
    return row_to(Package, row)


async def list_packages(pool: Any) -> list[Package]:
    async with pool.acquire() as c:
        rows = await c.fetch(f"SELECT {PACKAGE_COLS} FROM aut_packages ORDER BY name, created_at DESC")
    return [row_to(Package, r) for r in rows]


async def versions(pool: Any) -> dict[str, list[str]]:
    """Versions of each package name, newest upload first."""
    out: dict[str, list[str]] = {}
    for p in await list_packages(pool):
        out.setdefault(p.name, []).append(p.version)
    return out


async def get_package(pool: Any, name: str, version: str) -> Package | None:
    async with pool.acquire() as c:
        row = await c.fetchrow(
            f"SELECT {PACKAGE_COLS} FROM aut_packages WHERE name = $1 AND version = $2", name, version
        )
    return row_to(Package, row) if row is not None else None


async def content(pool: Any, name: str, version: str) -> bytes | None:
    async with pool.acquire() as c:
        data = await c.fetchval(
            "SELECT content FROM aut_packages WHERE name = $1 AND version = $2", name, version
        )
    return data if isinstance(data, bytes) else None
