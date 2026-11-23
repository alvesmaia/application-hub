"""App fixtures: HTTP client over the real app (lifespan included) and a throwaway database
(``HUB_DB_TEST_URL``). Packages run in real subprocesses; the samples' network is never reached
(the sample tests call their ``run()`` with a fake transport)."""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from hub_sdk.identity import Identity

DB_URL = os.environ.get("HUB_DB_TEST_URL", "")
TOKEN = "automations-token-de-teste-1234"
ANA = Identity("ana@demo.local", name="Ana").encode()
BIA = Identity("bia@demo.local", name="Bia").encode()
ADMIN = Identity("chefe@demo.local", roles=("Administrador",), name="Chefe").encode()
AS_ADMIN = {"x-hub-user": ADMIN}
AS_BIA = {"x-hub-user": BIA}
HUB = {"x-hub-internal-token": TOKEN}


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


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("HUB_APP_ID", "automations")
    monkeypatch.setenv("HUB_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("HUB_JOB_LEASE_S", "3")
    monkeypatch.delenv("AUTOMATIONS_ADMIN_ROLES", raising=False)
    monkeypatch.delenv("HUB_DB_URL", raising=False)
    monkeypatch.delenv("HUB_JOBS_MODE", raising=False)
    yield tmp_path


@pytest.fixture
async def db_url(env: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    name = f"aut_t_{secrets.token_hex(4)}"
    admin = await asyncpg.connect(DB_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = DB_URL.rpartition("/")[0] + "/" + name
    monkeypatch.setenv("HUB_DB_URL", url)
    try:
        yield url
    finally:
        admin = await asyncpg.connect(DB_URL)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


class Running:
    """App with an active lifespan and its client (as Ana, a regular user)."""

    def __init__(self, app: Any, client: httpx.AsyncClient) -> None:
        self.app = app
        self.client = client

    @property
    def st(self) -> Any:
        return self.app.state.automations

    def anon(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.client._transport, base_url="http://t")


async def start() -> AsyncIterator[Running]:
    from automations.app import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers={"x-hub-user": ANA}
        ) as c,
    ):
        yield Running(app, c)


@pytest.fixture
async def running(env: Path) -> AsyncIterator[Running]:
    async for r in start():
        yield r


@pytest.fixture
async def running_db(db_url: str) -> AsyncIterator[Running]:
    async for r in start():
        await wait_ready(r)
        yield r


async def wait_ready(r: Running, timeout: float = 30) -> None:
    """Waits for the migrations and the sample import (three processes)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        r1 = await r.client.get("/automations/api/v1/processes")
        if r1.status_code == 200 and len(r1.json()["items"]) == 3:
            return
        await asyncio.sleep(0.1)
    raise AssertionError("banco não ficou pronto")
