"""Fixtures: HTTP client over the real app (lifespan included), in-memory storage and a throwaway database
(``HUB_DB_TEST_URL``)."""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest

from .fakes import FakeStore

DB_URL = os.environ.get("HUB_DB_TEST_URL", "")
TOKEN = "fornecedores-token-de-teste-123456"
ANA = "eyJ1c2VyIjoiYW5hQGRlbW8ubG9jYWwiLCJyb2xlcyI6W10sIm5hbWUiOiJBbmEifQ"  # {"user":"ana@demo.local","name":"Ana"}
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"


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
    monkeypatch.setenv("HUB_APP_ID", "fornecedores")
    monkeypatch.setenv("HUB_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_INTERNAL_TOKEN", TOKEN)
    for k in ("HUB_DB_URL", "HUB_EXTAPI_URL", "HUB_S3_ENDPOINT", "HUB_CONNECTIONS", "HUB_STORAGE_URL"):
        monkeypatch.delenv(k, raising=False)
    yield tmp_path


class Running:
    """App with active lifespan + client + test storage."""

    def __init__(self, app: Any, client: httpx.AsyncClient, store: FakeStore | None) -> None:
        self.app = app
        self.client = client
        self.store = store

    def anon(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.client._transport, base_url="http://t")


async def _run(monkeypatch: pytest.MonkeyPatch, store: FakeStore | None) -> AsyncIterator[Running]:
    import fornecedores.app as core

    monkeypatch.setattr(core, "attachments_store", lambda: store)
    app = core.create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers={"x-hub-user": ANA}
        ) as c,
    ):
        yield Running(app, c, store)


@pytest.fixture
async def running(env: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Running]:
    async for r in _run(monkeypatch, FakeStore()):
        yield r


@pytest.fixture
async def bare(env: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Running]:
    """App without attachment storage."""
    async for r in _run(monkeypatch, None):
        yield r


@pytest.fixture
async def db_url(env: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    name = f"forn_t_{secrets.token_hex(4)}"
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
