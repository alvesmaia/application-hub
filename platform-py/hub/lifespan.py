"""Startup and shutdown of the hub: state database, initial variables, background jobs and the storage broker."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI

from hub.admin.config import DEV_INFRA_PASSWORDS
from hub.admin.store import AdminStore
from hub.auth import ABSOLUTE_MS, IDLE_MS, MAX_SESSIONS, AuthStore, roles_for
from hub.connections import broker as broker_mod
from hub.connections.connctx import broker_url
from hub.connections.store import ConnStore
from hub.gateway.identity import user_sig_key
from hub.ops.background import Background
from hub.state import Config as StateConfig
from hub.state import SessionLimits, SessionRow, StateDb


async def startup_admin(app: FastAPI) -> None:
    """Initial variables, each app's internal token (vault) and the connections' managed variables."""
    admin = cast(AdminStore, app.state.admin)
    actor = "plataforma"
    if path := os.getenv("HUB_APP_ENV_FILE"):
        with suppress(OSError, ValueError):
            n = await admin.cfg.import_bootstrap(
                actor, json.loads(await asyncio.to_thread(Path(path).read_text, encoding="utf-8"))
            )
            if n:
                print(f"hub: {n} variáveis iniciais importadas de {path}")

    def seed_of(app_id: str) -> str | None:
        tok = os.getenv("HUB_INTERNAL_TOKEN_" + app_id.upper().replace("-", "_"), "").strip()
        return tok if len(tok) >= 16 else None

    # Rotation of infrastructure passwords: dev values in the variables become the configured passwords.
    rotated = await admin.cfg.replace_secret_values(
        actor,
        {
            d: os.getenv(k, "").strip()
            for k, d in DEV_INFRA_PASSWORDS.items()
            if len(os.getenv(k, "").strip()) >= 16
        },
    )
    if rotated:
        print(f"hub: senhas de infraestrutura atualizadas em {len(rotated)} variável(is)")
    await admin.cfg.ensure_internal_tokens(actor, seed_of)
    await app.state.conns.sync_env(actor, broker_url())
    app.state.roles = roles_for(app.state.data_dir)


async def _attach_db(app: FastAPI) -> StateDb | None:
    """Connects the state database (if configured) and moves the file sessions into it."""
    auth_store = cast(AuthStore, app.state.auth)
    admin = cast(AdminStore, app.state.admin)
    conns = cast(ConnStore, app.state.conns)
    cfg = StateConfig.from_env()
    db = await StateDb.connect(cfg) if cfg else None
    auth_store.db = db
    admin.db, admin.chain.db, conns.db = db, db, db
    if (ops_store := getattr(app.state, "ops", None)) is not None:
        ops_store.db = db  # F19: `ops.json` under a lock across replicas
    await admin.reconcile()
    if db:
        async with auth_store.locked(True) as data:
            old_sessions = list(data["sessions"])
            for session in old_sessions:
                await db.session_create(
                    SessionRow(**session), SessionLimits(IDLE_MS, ABSOLUTE_MS, MAX_SESSIONS)
                )
            data["sessions"].clear()
    return db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: PLR0915
    db = await _attach_db(app)
    await app.state.auth.bootstrap()
    await startup_admin(app)
    insight, gateway = app.state.insight, app.state.gateway
    insight.start(gateway.client)
    app.state.start_dev_watch()
    bg = Background(app, gateway.client, app.state.data_dir)
    await bg.start(db, float(os.getenv("HUB_OUTBOX_POLL_S", "5") or 5))
    broker: Any = None
    if app.state.broker_enabled:
        broker = await broker_mod.serve(
            broker_mod.build_broker(
                app.state.conns, app.state.admin, gateway.opts.user_sig_key or user_sig_key()
            ),
            app.state.broker_addr,
        )
    yield
    if (w := getattr(app.state, "dev_watch", None)) is not None:
        w.cancel()
    await bg.stop()
    await insight.stop()
    if broker:
        await broker_mod.stop(*broker)
    await gateway.aclose()
    if db:
        await db.close()
