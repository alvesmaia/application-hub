"""Fixtures: throwaway Postgres via ``HUB_DB_TEST_URL`` (e.g. the ``hub-testdb`` container).

Tests marked ``@pytest.mark.db`` are skipped without the variable. Each test gets a
new database (``hubsdk_<random>``) deleted at the end.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from hub_sdk.db import create_pool
from hub_sdk.schema import migrate

DB_URL = os.environ.get("HUB_DB_TEST_URL", "")


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    if DB_URL:
        return
    if os.environ.get("CI", "").lower() == "true" and any("db" in it.keywords for it in items):
        # In CI the Postgres tests are mandatory: without a DB it is a failure, never "skipped".
        raise pytest.UsageError("CI=true sem HUB_DB_TEST_URL: os testes marcados @db não podem ser pulados")
    skip = pytest.mark.skip(reason="defina HUB_DB_TEST_URL para os testes com Postgres")
    for it in items:
        if "db" in it.keywords:
            it.add_marker(skip)


def with_db(url: str, name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{name}"


@pytest.fixture
async def db_url() -> AsyncIterator[str]:
    name = f"hubsdk_{secrets.token_hex(4)}"
    admin = await asyncpg.connect(DB_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    try:
        yield with_db(DB_URL, name)
    finally:
        admin = await asyncpg.connect(DB_URL)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


@pytest.fixture
async def pool(db_url: str) -> AsyncIterator[Any]:
    p = await create_pool(db_url, 8)
    async with p.acquire() as c:
        await migrate(c, "CREATE TABLE IF NOT EXISTS items (id serial PRIMARY KEY, name text NOT NULL)")
    try:
        yield p
    finally:
        await p.close()
