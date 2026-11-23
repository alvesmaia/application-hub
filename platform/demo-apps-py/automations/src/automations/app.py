"""Automações — a small automation orchestrator on the platform: **packages** (versioned zips of
Python code with an ``automation.json`` manifest) → **processes** (a package + selected version,
default params) → **triggers** (interval/daily schedules of a process) → **runs** (durable job
``automations.run``, also listed in the hub's Tasks page; the package runs in an isolated
subprocess).

Routes (under ``/automations``; the browser sees ``/apps/automations/...``):

- everyone: ``GET /automations`` · ``/catalog`` (enabled processes; "Executar" opens the form
  generated from the package fields) · ``POST /catalog/{id}/run`` (422 = form with errors;
  303 = run detail) · ``GET /runs`` (own runs; admins: all) · ``/runs/{id}`` (polls
  while not terminal) · ``POST /runs/{id}/rerun`` · ``…/cancel``;
- admins (``AUTOMATIONS_ADMIN_ROLES``; others get 403): ``/packages`` (upload), ``/processes``
  (new/edit, version select), ``/triggers`` (new/edit, enable, delete);
- ``GET /_skeleton`` · ``/assets/{demo.css,app.css}``; API at ``/automations/api/v1/*``.

Every write commits its audit event and the idempotency marker in its own transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from fastapi import FastAPI
from hub_sdk.app import HubSdk, scratch_dir
from hub_sdk.render import AppInfo, NavItem, Renderer

from automations import api, present, scheduler
from automations.model import SCHEMA_SQL, STATUS_CLASS, STATUS_LABELS, TRIGGER_LABELS
from automations.routes import admin_packages, admin_processes, admin_triggers, pages, runs
from automations.runner import RunHandler
from automations.search import make_search
from automations.seed import seed
from automations.state import APP_ID, HERE, State

USER_NAV = (NavItem("catalog", "Automações", "wrench"), NavItem("runs", "Execuções", "list"))
ADMIN_NAV = (
    *USER_NAV,
    NavItem("processes", "Processos", "settings"),
    NavItem("packages", "Pacotes", "box"),
    NavItem("triggers", "Gatilhos", "calendar"),
)

VIEW_HELPERS: dict[str, Any] = {
    "when": present.when,
    "duration": present.duration,
    "status_label": lambda s: STATUS_LABELS.get(s, s),
    "status_class": lambda s: STATUS_CLASS.get(s, "status-draft"),
    "trigger_label": lambda s: TRIGGER_LABELS.get(s, s),
}


def info(nav: Sequence[NavItem]) -> AppInfo:
    return AppInfo(APP_ID, "Automações", icon="wrench", color="#e4ebfb", ink="#2f4fa3", nav=tuple(nav),
                   tagline="Orquestrador de automações")  # fmt: skip


def renderer(nav: Sequence[NavItem]) -> Renderer:
    r = Renderer(info(nav), HERE / "templates")
    cast(Any, r.env.globals).update(VIEW_HELPERS)
    return r


def build_sdk() -> HubSdk:
    handler = RunHandler(scratch_dir(APP_ID))
    sdk = HubSdk.from_env(APP_ID, app_sql=SCHEMA_SQL, handlers=[handler], after_migrate=seed)
    handler.sdk = sdk
    return sdk


def create_app() -> FastAPI:
    sdk = build_sdk()
    sdk.search = make_search(sdk)
    app = FastAPI(lifespan=scheduler.lifespan(sdk), docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.state.automations = State(sdk, renderer(USER_NAV), renderer(ADMIN_NAV))
    for module in (runs, admin_packages, admin_processes, admin_triggers):
        app.include_router(module.router)
    # pages last: its catch-all ``/automations/{section}`` must not shadow the other routes.
    app.include_router(pages.router)
    api.install(app)
    return app
