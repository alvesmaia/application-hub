"""Seed load of the tickets (2 million × ``HUB_SEED_SCALE``), at startup or ``python -m servicos seed``."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import asyncpg
from hub_sdk.app import HubSdk

from servicos.model import REDATE_SQL, SCHEMA_SQL, SEED_CHUNK, SEED_SQL, STALE_SQL, TICKETS_BASE

log = logging.getLogger("servicos")


def seed_rows() -> int:
    """Seed rows: ``SERVICOS_SEED_ROWS`` or 2 million × ``HUB_SEED_SCALE``."""
    if os.environ.get("SERVICOS_SEED_ROWS", ""):
        try:
            return int(os.environ["SERVICOS_SEED_ROWS"])
        except ValueError:
            return 0
    try:
        scale = float(os.environ.get("HUB_SEED_SCALE", "1"))
    except ValueError:
        scale = 1.0
    return round(TICKETS_BASE * (scale if scale >= 0 else 1.0))


async def seed_into(c: Any, rows: int) -> None:
    """Idempotent, resumable seed load: batches of 50k generated in Postgres; a batch already present is skipped."""
    t0 = time.monotonic()
    total = str(max(rows, 1))
    sql = SEED_SQL.replace("{TOTAL}", total)
    for start in range(1, rows + 1, SEED_CHUNK):
        end = min(start + SEED_CHUNK - 1, rows)
        if not await c.fetchval("SELECT EXISTS (SELECT 1 FROM tickets WHERE id = $1)", end):
            await c.execute(sql, start, end)
    if await c.fetchval(STALE_SQL):
        await c.execute(REDATE_SQL.replace("{TOTAL}", total))
    await c.execute("ANALYZE tickets")
    log.warning("servicos: carga de %d chamados pronta (%.0f s)", rows, time.monotonic() - t0)


async def seed(sdk: HubSdk) -> None:
    rows = seed_rows()
    if rows <= 0 or sdk.pool is None:
        return
    async with sdk.pool.acquire() as c:
        await c.execute("SELECT pg_advisory_lock(424245)")
        try:
            await seed_into(c, rows)
        finally:
            await c.execute("SELECT pg_advisory_unlock(424245)")


async def run_seed() -> int:
    """``python -m servicos seed``: migrates the schema and loads the data (2 million × ``HUB_SEED_SCALE``)."""
    from hub_sdk.db import app_env

    url = app_env("servicos", "HUB_DB_URL")
    if url is None:
        print("defina HUB_DB_URL (ou HUB_DB_URL_SERVICOS)", flush=True)
        return 2
    c = await asyncpg.connect(url)
    try:
        await c.execute(SCHEMA_SQL)
        await seed_into(c, seed_rows())
    finally:
        await c.close()
    return 0
