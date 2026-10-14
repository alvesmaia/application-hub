"""Coletor — the platform's FastAPI app (5th demo app, Python stack): reading bench for
reading codes on a phone / barcode scanner, **with no JavaScript of its own**.

Three screens:

- **Scan** (phone): camera button (continuous reading), physical scanner field always
  ready, "Barcode" / "QR" toggle (remembered per user), large card for the
  last result (green/red + beep/vibration) and the last 10 readings;
- **Test sheet** (computer): the codes to read ("open the app" QR separately; Items,
  Locations, Orders, Mixed formats) and, alongside, **Live readings** — what the same
  user is reading on the phone, updated every second (``data-hub-trigger="every 1s"``);
- **Diagnostics**: the device's features.

Camera reading, keyboard-style reader, beep/vibration, offline queue and install are
**hub behaviors** (``data-hub-scan``, ``data-hub-wedge``, ``data-hub-feedback``,
``data-hub-offline-queue``, ``data-hub-install``, ``data-hub-diag`` — CONTRATO-APP §25).

Routes (under ``/coletor``; the browser sees ``/apps/coletor/...``; the hub fetches from
``/_frag/coletor/...``): ``GET /coletor`` · ``/escanear`` · ``POST /escanear/ler`` ·
``POST /escanear/prefs`` · ``GET /folha`` · ``GET /folha/leituras`` · ``POST /folha/limpar`` ·
``GET /diagnostico``; telas antigas (``/contagem``, ``/recebimento``, ``/consulta``,
``/insumos``) show the Scan (or Sheet) screen. API at ``/coletor/api/v1/*``.

Each reading writes the row, the audit event and the idempotency marker **in the same
transaction** (the hub's offline queue retries with the same ``Idempotency-Key``: one reading
repetida nunca entra duas vezes).
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import FastAPI
from hub_sdk.app import HubSdk
from hub_sdk.render import ICONS, AppInfo, NavItem, Renderer

from coletor import api, barcodes, i18n
from coletor.model import SCHEMA_SQL, catalog, locations, orders
from coletor.reads import FORMAT_LABELS
from coletor.routes import live, pages, scan, sheet
from coletor.search import make_search
from coletor.state import HERE, State

log = logging.getLogger("coletor")

APP_ID = "coletor"

ICONS.update(
    {
        "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"></path>'
        '<path d="M7 8v8M10 8v8M13 8v8M16 8v8"></path>',
        "phone": '<rect x="7" y="2" width="10" height="20" rx="2"></rect><path d="M11 18h2"></path>',
        "print": '<path d="M6 9V3h12v6M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path><rect x="6" y="14" width="12" height="8"></rect>',
        "camera": '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"></path><circle cx="12" cy="13" r="4"></circle>',
        "qr": '<rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect><path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 17h4v4"></path>',
    }
)

INFO = AppInfo(
    id=APP_ID,
    name="Coletor",
    tagline="Leitura de códigos",
    icon="scan",
    color="#ffc20e",
    ink="#1d2125",
    nav=(
        NavItem("escanear", "Escanear", "scan"),
        NavItem("folha", "Folha de teste", "print"),
        NavItem("diagnostico", "Diagnóstico", "phone"),
    ),
)


async def seed(sdk: HubSdk) -> None:
    """Idempotent initial load (catalog, locations and orders — only to name readings)."""
    if sdk.pool is None:
        return
    async with sdk.pool.acquire() as c, c.transaction():
        await c.execute("SELECT pg_advisory_xact_lock(424250)")
        if int(await c.fetchval("SELECT count(*) FROM col_items")) == 0:
            await c.executemany(
                "INSERT INTO col_items (code, code128, sku, name, unit, category) VALUES ($1,$2,$3,$4,$5,$6) "
                "ON CONFLICT DO NOTHING",
                [(i.code, i.code128, i.sku, i.name, i.unit, i.category) for i in catalog()],
            )
            await c.executemany(
                "INSERT INTO col_locations (code, name, zone) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                [(loc.code, loc.name, loc.zone) for loc in locations()],
            )
            for o in orders():
                await c.execute(
                    "INSERT INTO col_orders (code, supplier, offset_days, expected_date) "
                    "VALUES ($1,$2,$3,current_date + $3::int) ON CONFLICT DO NOTHING",
                    o.code,
                    o.supplier,
                    o.offset_days,
                )
                await c.executemany(
                    "INSERT INTO col_order_lines (order_code, item, expected) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                    [(o.code, item, q) for item, q in o.lines],
                )
            log.warning("coletor: carga inicial (catálogo, locais e pedidos)")
        await c.execute("UPDATE col_orders SET expected_date = current_date + offset_days")


def build_sdk() -> HubSdk:
    async def after(sdk: HubSdk) -> None:
        await seed(sdk)

    return HubSdk.from_env(APP_ID, app_sql=SCHEMA_SQL, handlers=[], after_migrate=after)


async def run_worker() -> None:
    await build_sdk().run_worker_process()


def create_app() -> FastAPI:
    sdk = build_sdk()
    sdk.search = make_search(sdk)
    render = Renderer(INFO, HERE / "templates")
    cast(Any, render.env.globals).update(code_svg=barcodes.svg, fmt_label=lambda f: FORMAT_LABELS.get(f, f))
    st = State(sdk, render)
    app = FastAPI(lifespan=sdk.lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    sdk.install(app)
    app.state.coletor = st
    app.include_router(scan.router)
    app.include_router(sheet.router)
    app.include_router(live.router)
    # pages last: its catch-all ``/coletor/{section}`` must not shadow the other routes.
    app.include_router(pages.router)
    api.install(app)
    return app


_ = i18n  # the translations catalog registers on import
