"""Fixtures: HTTP client over the real app (lifespan included) and a throwaway database
(``HUB_DB_TEST_URL``)."""

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
TOKEN = "servicos-token-de-teste-123456"
ANA = Identity("ana@demo.local", name="Ana").encode()
BOSS = Identity(
    "chefe@demo.local", name="Chefe", roles=("Administrador",), permissions=("aprovar_solicitacoes",)
).encode()
HUB = {"x-hub-internal-token": TOKEN}


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    if DB_URL:
        return
    if os.environ.get("CI", "").lower() == "true" and any("db" in it.keywords for it in items):
        raise pytest.UsageError("CI=true sem HUB_DB_TEST_URL: os testes marcados @db não podem ser pulados")
    skip = pytest.mark.skip(reason="defina HUB_DB_TEST_URL para os testes com Postgres")
    for it in items:
        if "db" in it.keywords:
            it.add_marker(skip)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("HUB_APP_ID", "servicos")
    monkeypatch.setenv("HUB_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_INTERNAL_TOKEN", TOKEN)
    monkeypatch.delenv("HUB_DB_URL", raising=False)
    monkeypatch.delenv("HUB_HEAVY_MAX", raising=False)
    yield tmp_path


@pytest.fixture
async def db_url(env: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    name = f"serv_t_{secrets.token_hex(4)}"
    admin = await asyncpg.connect(DB_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = DB_URL.rpartition("/")[0] + "/" + name
    monkeypatch.setenv("HUB_DB_URL", url)
    monkeypatch.setenv("SERVICOS_SEED_ROWS", "600")
    try:
        yield url
    finally:
        admin = await asyncpg.connect(DB_URL)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


class Running:
    """App with active lifespan + client."""

    def __init__(self, app: Any, client: httpx.AsyncClient) -> None:
        self.app = app
        self.client = client

    def anon(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.client._transport, base_url="http://t")


@pytest.fixture
async def running(env: Path) -> AsyncIterator[Running]:
    from servicos.app import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers={"x-hub-user": ANA}
        ) as c,
    ):
        yield Running(app, c)


async def wait_ready(c: httpx.AsyncClient, timeout: float = 30) -> None:
    """Waits for the migrations + initial load (600 tickets)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    last = ""
    while loop.time() < end:
        try:
            r = await c.get("/servicos/api/v1/tickets/600")
            if r.status_code == 200:
                return
            last = f"status={r.status_code} {r.text[:120]!r}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(0.2)
    raise AssertionError(f"banco não ficou pronto ({last})")
