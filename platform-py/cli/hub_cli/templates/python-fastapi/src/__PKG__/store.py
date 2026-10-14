"""App data in SQLAlchemy Core (SQLite under `hub dev`, Postgres on the hub — same API).

The schema changes only through an Alembic migration (``migrations/versions``); ``metadata`` mirrors the tables.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

metadata = sa.MetaData()

items = sa.Table(
    "items",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("name", sa.String(80), nullable=False),
    sa.Column("quantity", sa.Integer, nullable=False, server_default="0"),
)


def list_items(engine: sa.Engine) -> list[dict[str, Any]]:
    with engine.connect() as c:
        rows = c.execute(sa.select(items).order_by(items.c.id)).mappings().all()
    return [dict(r) for r in rows]


def add_item(engine: sa.Engine, name: str, quantity: int) -> dict[str, Any]:
    with engine.begin() as c:
        new_id = c.execute(sa.insert(items).values(name=name, quantity=quantity)).inserted_primary_key
    return {"id": int(new_id[0]) if new_id else 0, "name": name, "quantity": quantity}
