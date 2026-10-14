"""Asset management — the platform's FastAPI app (``HUB_DEMO_APP=ativos``; same routes, same markup, same database).

Routes (all under ``/ativos``; the hub exposes them at ``/_frag/ativos/...``):

- ``GET /ativos`` · ``/ativos/{dashboard,assets,movements,reports,settings}`` — fragmentos
  (app workspace: sidebar + content). ``assets`` is dynamic (Postgres): paginated list with
  search, filters and server-side sorting, panels ``?edit=<id>``, ``?new=1``, ``?import=1``;
  the other sections are the mockup fragments (``fragments/``);
- ``GET /ativos/_skeleton`` · ``/ativos/assets/{demo.css,app.css}`` (no JavaScript — wave 12);
- ``POST /ativos/assets`` (create) · ``POST /ativos/itens/{id}`` (save, with ``version`` →
  409 if someone else changed it first) · ``POST /ativos/itens/{id}/delete``;
- ``POST /ativos/itens/import`` (multipart) → durable job ``ativos.import``;
  ``GET …/import/{job}`` (progresso) · ``GET …/import/{job}/erros.csv``;
- ``GET /ativos/itens/export.csv`` (streaming, heavy operation) · ``POST …/export.xlsx`` →
  tarefa ``ativos.export_xlsx``; ``GET …/export/{job}`` · ``…/export/{job}/ativos.xlsx`` ·
  ``GET /ativos/itens/modelo.xlsx``;
- ``/ativos/api/v1/*`` (OpenAPI at ``/ativos/api/openapi.json``; MCP tools generated from it)
  and ``/ativos/_hub/*`` (hub-sdk internal endpoints, internal token).

Every action that writes data also writes the audit event to the outbox and the
idempotency marker **in the same transaction**.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import asyncpg
from fastapi import FastAPI
from hub_sdk import e2e_hostile
from hub_sdk.app import HubSdk, scratch_dir
from hub_sdk.jobs import Handler

from ativos import api, exporter, importer, saga
from ativos.i18n import I18nMiddleware
from ativos.model import SCHEMA_SQL
from ativos.routes import assets, exports, imports, pages
from ativos.search import make_search
from ativos.seed import seed
from ativos.state import State

log = logging.getLogger("ativos")

APP_ID = "ativos"


def build_sdk(scratch: Path) -> HubSdk:
    """App SDK (the same in the web process and in the jobs process)."""
    pool = saga.LazyPool()
    handlers: list[Handler] = [importer.ImportHandler(), exporter.ExportHandler(scratch)]
    sg = saga.build(pool)
    handlers.append(sg)  # type: ignore[arg-type]
    if saga.storage.connection(saga.DOCS) is not None:
        from hub_sdk.uploads import PromoteHandler

        up = saga.uploads_for(pool)
        if up is not None:
            handlers.append(PromoteHandler(up))

    async def sweep(sdk: HubSdk) -> None:
        await sweep_scratch(sdk, scratch)
        up2 = saga.uploads_for(sdk.pool) if sdk.pool is not None else None
        if up2 is not None:
            with contextlib.suppress(Exception):
                await up2.cleanup()

    sdk = HubSdk.from_env(
        APP_ID, app_sql=SCHEMA_SQL, handlers=handlers, after_migrate=seed, cleanup_hooks=[sweep]
    )
    pool.sdk = sdk
    return sdk


async def sweep_scratch(sdk: HubSdk, scratch: Path) -> int:
    """Cleanup of ``/scratch`` (at startup and every hour): exports older than 6 h, ``.part``
    orphans and import uploads that no active job has used for more than 1 h."""
    active: set[str] | None = None
    if sdk.db_ready and sdk.pool is not None:
        try:
            async with sdk.pool.acquire() as c:
                rows = await c.fetch(
                    "SELECT payload->>'file_path' AS p FROM hub_jobs WHERE kind = $1 AND status IN ('queued', 'running')",
                    importer.KIND,
                )
            active = {str(r["p"]) for r in rows if r["p"]}
        except (OSError, asyncpg.PostgresError) as e:
            log.warning("ativos: limpeza do scratch sem banco: %s", e)
    n = await asyncio.to_thread(exporter.cleanup_old_exports, scratch, 6 * 3600, active)
    if n:
        log.warning("ativos: limpeza do scratch: %d arquivo(s) antigo(s) apagado(s)", n)
    return n


async def run_worker() -> None:
    """``python -m ativos worker``: jobs process (``HUB_JOBS_MODE=process``)."""
    await build_sdk(scratch_dir(APP_ID)).run_worker_process()


def create_app() -> FastAPI:
    scratch = scratch_dir(APP_ID)
    sdk = build_sdk(scratch)
    sdk.search = make_search(sdk)
    st = State(sdk, scratch)
    app = FastAPI(lifespan=sdk.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.add_middleware(I18nMiddleware)
    app.state.ativos = st
    e2e_hostile.install(app, APP_ID)  # only with HUB_DEMO_E2E_HOSTILE=1 (tests)
    # Order matters: `/itens/import` and `/itens/export.*` before `/itens/{aid}` (assets), and the
    # catch-all `/ativos/{section}` (pages) last.
    app.include_router(imports.router)
    app.include_router(exports.router)
    app.include_router(assets.router)
    app.include_router(pages.router)
    # Wave 11: API v1 (OpenAPI at /ativos/api/openapi.json; internal MCP generated from it).
    api.install(app)
    return app
