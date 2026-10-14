"""Alembic environment: the URL comes from ``hub_sdk.db.upgrade`` (DATABASE_URL — SQLite in dev, Postgres on the hub)."""

import sqlalchemy as sa
from alembic import context

url = context.config.get_main_option("sqlalchemy.url") or ""
with sa.create_engine(url).connect() as conn:
    context.configure(connection=conn, render_as_batch=conn.dialect.name == "sqlite")
    with context.begin_transaction():
        context.run_migrations()
