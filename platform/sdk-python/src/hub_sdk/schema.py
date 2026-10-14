"""SDK tables, ids and DB utilities."""

from __future__ import annotations

import hmac
import secrets
from typing import Any

# ``SCHEMA_SQL``: idempotent, applied together with the app's
# migrations.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hub_jobs (
  id text PRIMARY KEY,
  kind text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key text,
  status text NOT NULL DEFAULT 'queued',
  attempts int NOT NULL DEFAULT 0,
  max_attempts int NOT NULL DEFAULT 3,
  max_duration_s int NOT NULL DEFAULT 3600,
  run_after timestamptz NOT NULL DEFAULT now(),
  lease_owner text,
  lease_until timestamptz,
  checkpoint jsonb,
  progress_done bigint NOT NULL DEFAULT 0,
  progress_total bigint NOT NULL DEFAULT 0,
  progress_text text NOT NULL DEFAULT '',
  result jsonb,
  error text,
  cancel_requested boolean NOT NULL DEFAULT false,
  user_name text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  started_at timestamptz,
  finished_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS hub_jobs_idem ON hub_jobs (kind, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS hub_jobs_claim ON hub_jobs (status, run_after);
ALTER TABLE hub_jobs ADD COLUMN IF NOT EXISTS attempt_token text;
CREATE TABLE IF NOT EXISTS hub_idempotency (
  route text NOT NULL,
  key text NOT NULL,
  request_hash text NOT NULL,
  state text NOT NULL,
  status int,
  headers jsonb,
  body bytea,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (route, key)
);
CREATE INDEX IF NOT EXISTS hub_idempotency_expires ON hub_idempotency (expires_at);
CREATE TABLE IF NOT EXISTS hub_outbox (
  id bigserial PRIMARY KEY,
  event_id text NOT NULL UNIQUE,
  kind text NOT NULL,
  user_name text NOT NULL DEFAULT '',
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  acked_at timestamptz
);
CREATE INDEX IF NOT EXISTS hub_outbox_pending ON hub_outbox (id) WHERE acked_at IS NULL;
CREATE TABLE IF NOT EXISTS hub_saga_once (
  key text PRIMARY KEY,
  output jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS hub_uploads (
  id text PRIMARY KEY,
  connection text NOT NULL,
  staging_path text NOT NULL,
  path text,
  state text NOT NULL DEFAULT 'pending',
  user_name text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  committed_at timestamptz,
  promoted_at timestamptz
);
CREATE INDEX IF NOT EXISTS hub_uploads_pending ON hub_uploads (expires_at) WHERE state = 'pending';
ALTER TABLE hub_uploads ADD COLUMN IF NOT EXISTS size bigint;
ALTER TABLE hub_uploads ADD COLUMN IF NOT EXISTS etag text;
"""

# Retention of the confirmed outbox.
APPS_PREFIX = "/apps"
"""Prefixo público das páginas dos apps (onda 13): o navegador vê ``/apps/{id}/…``; o app
continua servindo ``/{id}/…`` (o hub traduz). Links do app usam :func:`base`."""


def base(app_id: str) -> str:
    """App's public base (``/apps/{id}``): prefix of every app link in the HTML."""
    return f"{APPS_PREFIX}/{app_id}"


OUTBOX_RETENTION = "7 days"

# Migration lock (two processes of the same app — or a
# seed and the app — do not migrate at the same time).
MIGRATION_LOCK = 424242


def new_id(prefix: str) -> str:
    """128-bit random identifier in hex with a prefix (`evt-…`, `job-…`)."""
    return f"{prefix}-{secrets.token_hex(16)}"


def ct_eq(a: str | bytes, b: str | bytes) -> bool:
    """Constant-time comparison (tokens)."""
    ab = a.encode() if isinstance(a, str) else a
    bb = b.encode() if isinstance(b, str) else b
    return hmac.compare_digest(ab, bb)


async def migrate(conn: Any, app_sql: str = "") -> None:
    """Applies the app's migrations + :data:`SCHEMA_SQL` under an advisory lock."""
    await conn.execute(f"SELECT pg_advisory_lock({MIGRATION_LOCK})")
    try:
        if app_sql.strip():
            await conn.execute(app_sql)
        await conn.execute(SCHEMA_SQL)
    finally:
        await conn.execute(f"SELECT pg_advisory_unlock({MIGRATION_LOCK})")


async def cleanup(pool: Any) -> tuple[int, int]:
    """Deletes expired idempotency records and outbox entries confirmed more than 7 days ago.

    Returns the (idempotency, outbox) counts deleted.
    """
    async with pool.acquire() as c:
        idem = await c.execute("DELETE FROM hub_idempotency WHERE expires_at < now()")
        outbox = await c.execute(
            "DELETE FROM hub_outbox WHERE acked_at IS NOT NULL "  # noqa: S608 -- SQL uses bound values and fixed internal fragments
            f"AND acked_at < now() - interval '{OUTBOX_RETENTION}'"
        )
    return _count(idem), _count(outbox)


def _count(status: str) -> int:
    """`DELETE 12` → 12 (asyncpg status)."""
    try:
        return int(str(status).rsplit(" ", 1)[-1])
    except ValueError:
        return 0
