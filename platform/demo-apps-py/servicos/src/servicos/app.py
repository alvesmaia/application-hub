"""Service Center — the platform's FastAPI app (``HUB_DEMO_APP=servicos``; same routes, same markup, same database).

Tickets in Postgres (millions of rows, high write volume), aggregated management report
with CSV export and a per-user session cache in memory (bounded LRU).

Routes (all under ``/servicos``; the hub exposes them at ``/_frag/servicos/...``):

- ``GET /servicos`` · ``/servicos/{dashboard,approvals,knowledge,settings}`` — mockup fragments
  (``fragments/``), all with the extra "Reports" item in the sidebar;
- ``GET /servicos/requests?status=&categoria=&prioridade=&protocolo=&antes=`` (key-based pagination,
  fast at any depth) and ``?new=1``; ``POST /servicos/requests`` (open) and
  ``POST /servicos/requests/{id}`` (change status, with ``version``: outdated → 409);
- ``GET /servicos/reports?de=&ate=&agrupar=&categoria=`` and ``…/reports/export.csv`` (heavy operation);
- ``GET /servicos/_skeleton`` · ``/servicos/assets/{demo.css,app.css}`` (no JavaScript);
- ``/servicos/api/v1/*`` (OpenAPI at ``/servicos/api/openapi.json``; MCP tools generated from it) and
  ``/servicos/_hub/*`` (hub-sdk internal endpoints, including ``approvals`` — Inbox decision).

Every action that writes data also writes the audit event to the outbox and the idempotency marker
**in the same transaction** (opening with "Needs approval" publishes the request to the hub Inbox).
"""

from __future__ import annotations

from fastapi import FastAPI
from hub_sdk import e2e_hostile
from hub_sdk.app import HubSdk, scratch_dir

from servicos import api
from servicos.approvals import make_approvals
from servicos.i18n import I18nMiddleware
from servicos.model import SCHEMA_SQL
from servicos.routes import pages, reports, tickets
from servicos.search import make_search
from servicos.seed import seed
from servicos.session import Sessions
from servicos.state import State

APP_ID = "servicos"


def create_app() -> FastAPI:
    scratch = scratch_dir(APP_ID)
    sdk = HubSdk.from_env(APP_ID, app_sql=SCHEMA_SQL, after_migrate=seed)
    sdk.search = make_search(sdk)
    sdk.approvals = make_approvals(sdk)
    st = State(sdk, scratch, Sessions.from_env())
    app = FastAPI(lifespan=sdk.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.add_middleware(I18nMiddleware)
    app.state.servicos = st
    e2e_hostile.install(app, APP_ID)  # only with HUB_DEMO_E2E_HOSTILE=1 (tests)
    app.include_router(tickets.router)
    app.include_router(reports.router)
    # pages last: its catch-all ``/servicos/{section}`` must not shadow the other routes.
    app.include_router(pages.router)
    # Wave 11: API v1 (OpenAPI at /servicos/api/openapi.json; internal MCP generated from it).
    api.install(app)
    return app
