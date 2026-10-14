"""Items table."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("items")
