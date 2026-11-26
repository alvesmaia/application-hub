"""Service Center — the platform's FastAPI app (``HUB_DEMO_APP=servicedesk``; same routes, same markup, same database).

Tickets in Postgres (millions of rows, high write volume), aggregated management report
with CSV export and a per-user session cache in memory (bounded LRU).

Routes (all under ``/servicedesk``; the hub exposes them at ``/_frag/servicedesk/...``):

- ``GET /servicedesk`` · ``/servicedesk/{dashboard,approvals,knowledge,settings}`` — mockup fragments
  (``fragments/``), all with the extra "Reports" item in the sidebar;
- ``GET /servicedesk/requests?status=&categoria=&prioridade=&protocolo=&antes=`` (key-based pagination,
  fast at any depth) and ``?new=1``; ``POST /servicedesk/requests`` (open) and
  ``POST /servicedesk/requests/{id}`` (change status, with ``version``: outdated → 409);
- ``GET /servicedesk/reports?de=&ate=&agrupar=&categoria=`` and ``…/reports/export.csv`` (heavy operation);
- ``GET /servicedesk/_skeleton`` · ``/servicedesk/assets/{demo.css,app.css}`` (no JavaScript);
- ``/servicedesk/api/v1/*`` (OpenAPI at ``/servicedesk/api/openapi.json``; MCP tools generated from it) and
  ``/servicedesk/_hub/*`` (hub-sdk internal endpoints, including ``approvals`` — Inbox decision).

Every action that writes data also writes the audit event to the outbox and the idempotency marker
**in the same transaction** (opening with "Needs approval" publishes the request to the hub Inbox).
"""

from __future__ import annotations

from fastapi import FastAPI
from hub_sdk import e2e_hostile
from hub_sdk.app import HubSdk, scratch_dir

from servicedesk import api
from servicedesk.approvals import make_approvals
from servicedesk.i18n import I18nMiddleware
from servicedesk.model import SCHEMA_SQL
from servicedesk.routes import pages, reports, tickets
from servicedesk.seed import seed
from servicedesk.session import Sessions
from servicedesk.state import State

APP_ID = "servicedesk"


def create_app() -> FastAPI:
    scratch = scratch_dir(APP_ID)
    sdk = HubSdk.from_env(APP_ID, app_sql=SCHEMA_SQL, after_migrate=seed)
    sdk.approvals = make_approvals(sdk)
    st = State(sdk, scratch, Sessions.from_env())
    app = FastAPI(lifespan=sdk.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.add_middleware(I18nMiddleware)
    app.state.servicedesk = st
    e2e_hostile.install(app, APP_ID)  # only with HUB_DEMO_E2E_HOSTILE=1 (tests)
    app.include_router(tickets.router)
    app.include_router(reports.router)
    # pages last: its catch-all ``/servicedesk/{section}`` must not shadow the other routes.
    app.include_router(pages.router)
    # Wave 11: API v1 (OpenAPI at /servicedesk/api/openapi.json; internal MCP generated from it).
    api.install(app)
    return app
