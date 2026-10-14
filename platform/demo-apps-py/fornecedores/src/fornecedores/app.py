"""Supplier Network — the platform's FastAPI app (``HUB_DEMO_APP=fornecedores``; same routes and same markup).

Routes (all under ``/fornecedores``; the hub exposes them at ``/_frag/fornecedores/...``):

- ``GET /fornecedores`` · ``/fornecedores/{dashboard,indicators,analysis,reports,settings}`` —
  mockup fragments (``fragments/``); ``indicators?id=FOR-0284`` inserts the supplier record
  (registry lookup + attachments) above the directory summary;
- ``GET /fornecedores/_skeleton`` · ``/fornecedores/assets/{demo.css,app.css}`` (no JavaScript);
- ``POST /fornecedores/suppliers/{code}/lookup`` — registry lookup (external API with
  timeout, novas tentativas e disjuntor);
- ``POST …/attachments`` (multipart) · ``GET …/attachments/{name}`` (streaming download) ·
  ``POST …/attachments/{name}/delete`` — attachments in ``hub_sdk.storage`` (connection
  ``fornecedores-anexos`` of the hub, or the app's own S3 bucket ``HUB_S3_*``);
- ``GET …/ficha.pdf`` — supplier record as PDF (heavy operation: 429 without a slot);
- ``/fornecedores/api/v1/*`` (OpenAPI at ``/fornecedores/api/openapi.json``; MCP tools
  generated from it) and ``/fornecedores/_hub/*`` (hub-sdk internal endpoints, internal token).

Every action that changes data writes an audit event to the app's outbox (Postgres if there is
``HUB_DB_URL``; otherwise in memory) without delaying the response.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from hub_sdk import storage
from hub_sdk.app import HubSdk, scratch_dir
from hub_sdk.db import app_env

from fornecedores import api
from fornecedores.i18n import I18nMiddleware
from fornecedores.lookup import Lookup
from fornecedores.routes import pages, suppliers
from fornecedores.search import search
from fornecedores.state import State

log = logging.getLogger("fornecedores")

APP_ID = "fornecedores"
ATTACHMENTS_CONNECTION = "fornecedores-anexos"


def s3_config() -> storage.S3Config | None:
    """App's own bucket (``HUB_S3_ENDPOINT``, ``HUB_S3_BUCKET``, keys, region)."""
    endpoint, bucket = app_env(APP_ID, "HUB_S3_ENDPOINT"), app_env(APP_ID, "HUB_S3_BUCKET")
    ak, sk = app_env(APP_ID, "HUB_S3_ACCESS_KEY"), app_env(APP_ID, "HUB_S3_SECRET_KEY")
    if endpoint is None or bucket is None or ak is None or sk is None:
        return None
    return storage.S3Config(endpoint, bucket, ak, sk, app_env(APP_ID, "HUB_S3_REGION") or "us-east-1")


def attachments_store() -> storage.Connection | None:
    """Where the attachments live: the ``fornecedores-anexos`` connection (hub broker) if linked; otherwise the
    app's own bucket; otherwise none."""
    if (c := storage.connection(ATTACHMENTS_CONNECTION)) is not None:
        log.warning(
            "fornecedores: anexos na conexão %s (%s) pelo broker do hub", ATTACHMENTS_CONNECTION, c.kind
        )
        return c
    if (cfg := s3_config()) is None:
        return None
    try:
        return storage.s3_direct("s3-app", cfg)
    except storage.StorageError as e:
        log.error("fornecedores: object store: %s", e.message)
        return None


def create_app() -> FastAPI:
    scratch = scratch_dir(APP_ID)
    sdk = HubSdk.from_env(APP_ID)
    sdk.search = search
    st = State(sdk, scratch, attachments_store(), Lookup.from_env(APP_ID))
    app = FastAPI(lifespan=sdk.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.add_middleware(I18nMiddleware)
    app.state.fornecedores = st
    # pages last: its catch-all ``/fornecedores/{section}`` must not shadow the other routes.
    app.include_router(suppliers.router)
    app.include_router(pages.router)
    # Wave 11: API v1 (OpenAPI at /fornecedores/api/openapi.json; internal MCP generated from it).
    api.install(app)
    return app
