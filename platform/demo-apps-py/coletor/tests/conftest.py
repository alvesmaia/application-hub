"""Fixtures do app: cliente HTTP sobre o app real (lifespan incluído) e banco
descartável (``HUB_DB_TEST_URL``, ex.: container ``hub-testdb``)."""

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

DB_URL = os.environ.get("HUB_DB_TEST_URL", "")
TOKEN = "coletor-token-de-teste-1234567"
ANA = "eyJ1c2VyIjoiYW5hQGRlbW8ubG9jYWwiLCJyb2xlcyI6W10sIm5hbWUiOiJBbmEifQ"  # {"user":"ana@demo.local","name":"Ana"}


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    if DB_URL:
        return
    if os.environ.get("CI", "").lower() == "true" and any("db" in it.keywords for it in items):
        # No CI os testes com Postgres são obrigatórios: sem banco é falha, nunca "pulado".
        raise pytest.UsageError("CI=true sem HUB_DB_TEST_URL: os testes marcados @db não podem ser pulados")
    skip = pytest.mark.skip(reason="defina HUB_DB_TEST_URL para os testes com Postgres")
    for it in items:
        if "db" in it.keywords:
            it.add_marker(skip)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("HUB_APP_ID", "coletor")
    monkeypatch.setenv("HUB_SCRATCH_DIR", str(tmp_path))
    monkeypatch.setenv("HUB_INTERNAL_TOKEN", TOKEN)
    monkeypatch.setenv("HUB_JOB_LEASE_S", "3")
    monkeypatch.delenv("HUB_DB_URL", raising=False)
    yield tmp_path


@pytest.fixture
async def db_url(env: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[str]:
    name = f"coletor_t_{secrets.token_hex(4)}"
    admin = await asyncpg.connect(DB_URL)
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    url = DB_URL.rpartition("/")[0] + "/" + name
    monkeypatch.setenv("HUB_DB_URL", url)
    monkeypatch.delenv("COLETOR_ATIVOS_TOKEN", raising=False)
    try:
        yield url
    finally:
        admin = await asyncpg.connect(DB_URL)
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


class Running:
    """App com lifespan ativo + cliente."""

    def __init__(self, app: Any, client: httpx.AsyncClient) -> None:
        self.app = app
        self.client = client


@pytest.fixture
async def running(env: Path) -> AsyncIterator[Running]:
    from coletor.app import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t", headers={"x-hub-user": ANA}
        ) as c,
    ):
        yield Running(app, c)


async def wait_ready(c: httpx.AsyncClient, timeout: float = 30) -> None:
    """Espera as migrações + carga inicial (o local LOC-C04 é reconhecido)."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    last = ""
    while loop.time() < end:
        try:
            r = await c.get("/coletor/escanear?codigo=LOC-C04")
            if "Local: LOC-C04" in r.text:
                return
            last = f"status={r.status_code}"
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(0.2)
    raise AssertionError(f"banco não ficou pronto ({last})")
