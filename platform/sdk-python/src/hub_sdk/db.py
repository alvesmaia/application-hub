"""Pool do Postgres (asyncpg) com os codecs que o SDK espera e migração em segundo plano.

- ``HUB_DB_URL`` (ou ``HUB_DB_URL_<ID>``): URL do banco do app, injetada pela plataforma.
- Pools separados (mesma URL), como no Rust: handlers (``HUB_DB_POOL``, 12),
  idempotência (``HUB_IDEM_POOL``, 12) e tarefas (``HUB_JOBS_POOL``, 5) — uma rajada de
  requisições não trava o heartbeat do lease nem se autobloqueia.
- **Orçamento de conexões**: o papel do app tem ``CONNECTION LIMIT 40`` no hub-db. Os
  tamanhos padrão são **por processo**, então (medido na onda 7: 2 processos uvicorn + o de
  tarefas passaram de 40 e 12,6% das requisições falharam com *too many connections*):
  com ``UVICORN_WORKERS=N`` os padrões do web são divididos por N; no modo
  ``HUB_JOBS_MODE=process`` o web usa 2 conexões de tarefas (só enfileira/consulta) e o
  processo de tarefas abre só handlers (4) + tarefas (5), sem pool de idempotência.
  Padrão: web 12 + 12 + 2 (+ tarefas 4 + 5) = 35 ≤ 40. Variáveis explícitas prevalecem.

``json``/``jsonb`` entram e saem como objetos Python (codec registrado em cada conexão):
toda conexão passada ao SDK deve vir de :func:`create_pool`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import asyncpg

from hub_sdk.schema import migrate

log = logging.getLogger("hub_sdk.db")


def app_env(app_id: str, name: str) -> str | None:
    """``NOME_<ID>`` com fallback para ``NOME`` (vazio = ausente)."""
    for k in (f"{name}_{app_id.upper().replace('-', '_')}", name):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    return None


async def _init(conn: Any) -> None:
    for t in ("json", "jsonb"):
        await conn.set_type_codec(t, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(url: str, size: int) -> Any:
    """Pool preguiçoso (``min_size=0``): o app sobe mesmo com o banco fora do ar."""
    return await asyncpg.create_pool(
        dsn=url,
        min_size=0,
        max_size=max(1, size),
        init=_init,
        command_timeout=None,
        max_inactive_connection_lifetime=300,
    )


def pool_size(var: str, default: int, per_web_process: bool = False) -> int:
    """``var`` do ambiente; senão ``default`` — dividido pelos processos web
    (``UVICORN_WORKERS``) quando ``per_web_process``."""
    raw = os.environ.get(var, "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    if per_web_process:
        try:
            n = max(1, int(os.environ.get("UVICORN_WORKERS", "1") or 1))
        except ValueError:
            n = 1
        return max(2, default // n)
    return default


class Database:
    """Pools + estado "pronto" (migrações aplicadas)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.pool: Any = None
        self.idem_pool: Any = None
        self.jobs_pool: Any = None
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    async def open(self, role: str = "web", jobs_inline: bool = True) -> None:
        """``role="web"``: handlers + idempotência + tarefas (2 se as tarefas rodam em outro
        processo); ``role="worker"``: só handlers (4) e tarefas (5)."""
        if role == "worker":
            self.pool = await create_pool(self.url, pool_size("HUB_WORKER_DB_POOL", 4))
            self.jobs_pool = await create_pool(self.url, pool_size("HUB_JOBS_POOL", 5))
            return
        self.pool = await create_pool(self.url, pool_size("HUB_DB_POOL", 12, per_web_process=True))
        self.idem_pool = await create_pool(self.url, pool_size("HUB_IDEM_POOL", 12, per_web_process=True))
        jobs = pool_size("HUB_JOBS_POOL", 5, per_web_process=True) if jobs_inline else 2
        self.jobs_pool = await create_pool(self.url, jobs)

    def spawn_migrations(self, app_id: str, app_sql: str, after: Any = None) -> None:
        """Migra em segundo plano até conseguir (não segura a partida nem o health
        check); ``after`` (corrotina opcional) roda depois — ex.: carga inicial."""

        async def loop() -> None:
            wait = 0.5
            while True:
                try:
                    async with self.pool.acquire() as c:
                        await migrate(c, app_sql)
                    log.warning("%s: banco pronto (migrações aplicadas)", app_id)
                    self._ready.set()
                    break
                except Exception as e:  # banco fora, rede, credencial: tenta de novo
                    log.warning("%s: banco indisponível (%s); nova tentativa em %.1f s", app_id, e, wait)
                    await asyncio.sleep(wait)
                    wait = min(wait * 2, 10.0)
            if after is not None:
                try:
                    await after()
                except Exception:
                    log.exception("%s: pós-migração falhou", app_id)

        self._task = asyncio.create_task(loop())

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        for p in (self.pool, self.idem_pool, self.jobs_pool):
            if p is not None:
                try:
                    await asyncio.wait_for(p.close(), 5)
                except (TimeoutError, OSError):
                    p.terminate()


# --- Camada SQL portátil (SQLAlchemy Core + Alembic) -------------------------------------------
# Apps novos (template `python-fastapi` do `hub project create`): o mesmo código roda em SQLite no
# `hub dev` e em Postgres no hub. URL de ``DATABASE_URL`` (fallback ``HUB_DB_URL``). Extra ``hub-sdk[sql]``.


def database_url(default: str = "sqlite:///.hub/dev.db") -> str:
    """URL SQLAlchemy do banco do app: ``DATABASE_URL`` > ``HUB_DB_URL`` > ``default``
    (``postgres(ql)://`` vira ``postgresql+psycopg://``, o driver do extra ``sql``)."""
    url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("HUB_DB_URL", "").strip() or default
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def engine(url: str | None = None) -> Any:
    """Engine síncrona do SQLAlchemy Core. Em SQLite cria a pasta do arquivo."""
    import sqlalchemy as sa

    return sa.create_engine(_sqlite_dir(url or database_url()), pool_pre_ping=True)


def _sqlite_dir(u: str) -> str:
    from pathlib import Path

    if u.startswith("sqlite:///") and ":memory:" not in u:
        Path(u.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    return u


def upgrade(migrations_dir: str | os.PathLike[str], url: str | None = None, revision: str = "head") -> None:
    """Aplica as migrações Alembic de ``migrations_dir`` (pasta com ``env.py`` e ``versions/``)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir))
    cfg.set_main_option("sqlalchemy.url", _sqlite_dir(url or database_url()).replace("%", "%%"))
    command.upgrade(cfg, revision)
