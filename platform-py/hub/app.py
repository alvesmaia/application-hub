"""FastAPI app factory: assembles the stores, the shell state (`WebCtx`), middlewares and every route module."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates

from hub import devmode, gate, middleware, routes_login, routes_me, routes_static
from hub.admin.routes import mount_admin
from hub.admin.store import AdminStore
from hub.api.config import load_cfg
from hub.api.cors import Cors
from hub.api.mcp import mount_mcp
from hub.api.oauth import OAuth, mount_oauth
from hub.api.records import Records
from hub.api.v1 import mount_v1
from hub.auth import AuthStore, roles_for
from hub.config import Settings, load_settings
from hub.connections.provision import ObjectStore
from hub.connections.routes import mount_connections
from hub.connections.store import ConnStore
from hub.gateway.identity import user_sig_key
from hub.gateway.proxy import Gateway, GwOptions
from hub.gateway.registry import load_registry
from hub.gateway.routes import mount_gateway
from hub.insight.routes import mount_insight
from hub.insight.runtime import Insight
from hub.lifespan import lifespan
from hub.ops.newapp import load_dynamic
from hub.ops.routes import mount_ops
from hub.ux import search as srch
from hub.ux.account_routes import mount_account
from hub.ux.inbox import InboxStore
from hub.ux.inbox_routes import mount_inbox
from hub.ux.routes import icon, mount_ux
from hub.ux.store import UxStore
from hub.vault import Vault
from hub.webctx import STAGING_BAR_CSS as STAGING_BAR_CSS
from hub.webctx import WebCtx
from hub.webctx import initials as initials

_WEB = Path(__file__).parent / "web"


def create_app(settings: Settings | None = None, gateway: Gateway | None = None) -> FastAPI:  # noqa: PLR0915
    devmode.startup_check()
    settings = settings or load_settings()
    apicfg = load_cfg(settings.config)
    gateway = gateway or Gateway(load_registry(), GwOptions(user_sig_key=user_sig_key()))
    data_dir = Path(os.getenv("HUB_DATA_DIR", Path(__file__).resolve().parents[2] / "platform/data"))
    load_dynamic(data_dir, gateway.registry)  # F22: new apps deployed by the hub
    auth_store = AuthStore(data_dir / "auth.json")
    vault = Vault.open(data_dir)
    admin = AdminStore(data_dir, vault, apps=gateway.registry.ids())
    conns = ConnStore(data_dir, vault, admin)
    broker_addr = os.getenv("HUB_BROKER_ADDR", "").strip()
    srch.token_source = lambda app_id: admin.tokens.get(app_id)

    app = FastAPI(title=settings.brand, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.admin, app.state.conns, app.state.broker_enabled = (
        admin,
        conns,
        broker_addr not in ("", "0", "off"),
    )
    app.state.apicfg, app.state.broker_addr = apicfg, broker_addr
    app.state.start_dev_watch = devmode.mount(app)
    app.state.records = Records(lambda: auth_store.db)
    app.state.oauth = OAuth(apicfg, auth_store, admin, app.state.records)
    app.state.auth = auth_store
    app.state.roles = roles_for(data_dir)
    app.state.login_failures = {}
    app.state.gateway, app.state.data_dir = gateway, data_dir
    app.state.ux = UxStore(data_dir / "ux.json")
    app.state.inbox = InboxStore(data_dir / "inbox.json")
    w = _shell(
        app, settings, gateway, auth_store, Insight(data_dir, vault, admin, gateway.registry, app.state.ux)
    )
    w.load_sso(settings.config, vault)
    for r in (routes_static.router, routes_login.router, routes_me.router):
        app.include_router(r)
    _mount_sections(app, w, conns, admin)
    app.add_middleware(Cors, mcp_path=apicfg.mcp.path)
    return app


def _shell(
    app: FastAPI, settings: Settings, gateway: Gateway, auth_store: AuthStore, insight: Insight
) -> WebCtx:
    """Templates, insight headers to the apps, the shell state and its middlewares (in this order)."""
    templates = Jinja2Templates(directory=_WEB / "templates")
    cast(dict[str, Any], templates.env.globals)["dev_reload"] = devmode.reload_enabled
    app.state.insight = insight
    gateway.extra_headers = lambda request, app_id, ident: insight.upstream_headers(
        request, app_id, ident.user, list(ident.roles)
    )
    w = WebCtx(app, settings, templates, gateway, auth_store, insight, app.state.data_dir)
    app.state.web = w
    for mw in (middleware.translate_errors, middleware.security_headers, gate.authentication):
        app.middleware("http")(mw)
    return w


def _mount_sections(app: FastAPI, w: WebCtx, conns: ConnStore, admin: AdminStore) -> None:
    templates, page_ctx, gateway, insight, data_dir = (
        w.templates,
        w.page_ctx,
        w.gateway,
        w.insight,
        w.data_dir,
    )
    mount_inbox(app, templates, page_ctx, gateway.client)
    mount_account(app, templates, page_ctx, icon)
    ux_track = mount_ux(
        app, gateway.registry, templates, page_ctx, app.state.ux, data_dir, lambda: w.public_status() or set()
    )

    def track(request: Request, status_code: int) -> None:
        ux_track(request, status_code)
        allowed = getattr(request.state, "apps", None)
        insight.track(request, status_code, lambda a: allowed is None or a in allowed)

    app.state.ux_track = track
    mount_admin(
        app,
        templates,
        page_ctx,
        icon,
        gateway.registry,
        lambda: setattr(app.state, "roles", roles_for(data_dir)),
    )
    mount_connections(app, templates, page_ctx, icon, conns, admin, ObjectStore())
    mount_insight(app, templates, page_ctx, icon, insight, gateway.client)
    mount_ops(app, templates, page_ctx, icon, gateway.client, data_dir)
    app.state.ops_backups.conns = conns  # off-site backup copy to a SharePoint connection
    mount_v1(app)
    mount_mcp(app)
    mount_oauth(app, templates)
    mount_gateway(app, gateway, templates, w.app_page_ctx, insight.observed)
