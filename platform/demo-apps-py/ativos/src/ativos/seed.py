"""Seed load of the assets (300k × ``HUB_SEED_SCALE``), at startup or ``python -m ativos seed``."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import asyncpg
from hub_sdk.app import HubSdk

from ativos.model import SCHEMA_SQL, SEED_SQL

log = logging.getLogger("ativos")

ASSETS_BASE = 300_000
SEED_CHUNK = 50_000


def int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def seed_rows() -> int:
    """Seed rows: ``ATIVOS_SEED_ROWS`` or 300k × ``HUB_SEED_SCALE``."""
    if os.environ.get("ATIVOS_SEED_ROWS", ""):
        return int_env("ATIVOS_SEED_ROWS", 0)
    try:
        scale = float(os.environ.get("HUB_SEED_SCALE", "1"))
    except ValueError:
        scale = 1.0
    return round(ASSETS_BASE * scale) if scale >= 0 else ASSETS_BASE


async def seed_into(c: Any, rows: int) -> None:
    """Idempotent, resumable seed load: batches of 50k generated in Postgres; a batch already present is
    skipped (its last row's asset tag is checked)."""
    t0 = time.monotonic()
    for start in range(1, rows + 1, SEED_CHUNK):
        end = min(start + SEED_CHUNK - 1, rows)
        last = f"PT-{2016 + end % 10}-{end:07d}"
        if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM assets WHERE tag = $1)", last):
            await c.execute(SEED_SQL, start, end)
    await c.execute("ANALYZE assets")
    log.warning("ativos: carga de %d ativos pronta (%.0f s)", rows, time.monotonic() - t0)


async def seed(sdk: HubSdk) -> None:
    rows = seed_rows()
    if rows <= 0 or sdk.pool is None:
        return
    async with sdk.pool.acquire() as c:
        await c.execute("SELECT pg_advisory_lock(424244)")
        try:
            await seed_into(c, rows)
        finally:
            await c.execute("SELECT pg_advisory_unlock(424244)")


async def run_seed() -> int:
    """``python -m ativos seed``: migra o esquema e faz a carga (300 mil × ``HUB_SEED_SCALE``) fora
    of the app — the equivalent of the one-shot ``hub-seed`` service. Idempotent and resumable."""
    from hub_sdk.db import app_env

    url = app_env("ativos", "HUB_DB_URL")
    if url is None:
        print("defina HUB_DB_URL (ou HUB_DB_URL_ATIVOS)", flush=True)
        return 2
    c = await asyncpg.connect(url)
    try:
        await c.execute(SCHEMA_SQL)
        await seed_into(c, seed_rows())
    finally:
        await c.close()
    return 0
