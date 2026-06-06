"""product update publish metadata

Revision ID: pu20260606b2
Revises: b8e2d4f6a1c3
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "pu20260606b2"
down_revision: Union[str, Sequence[str], None] = "b8e2d4f6a1c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("product_updates", sa.Column("dedupe_key", sa.String(length=128), nullable=True))
    op.add_column("product_updates", sa.Column("created_by_user_id", sa.String(length=64), nullable=True))
    op.add_column(
        "product_updates",
        sa.Column("published_via", sa.String(length=32), nullable=False, server_default=sa.text("'admin_api'")),
    )
    op.add_column(
        "product_updates",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_check_constraint(
        "ck_product_updates_dedupe_key",
        "product_updates",
        "dedupe_key IS NULL OR length(btrim(dedupe_key)) > 0",
    )
    op.create_unique_constraint("uq_product_updates_dedupe_key", "product_updates", ["dedupe_key"])


def downgrade() -> None:
    op.drop_constraint("uq_product_updates_dedupe_key", "product_updates", type_="unique")
    op.drop_constraint("ck_product_updates_dedupe_key", "product_updates", type_="check")
    op.drop_column("product_updates", "published_at")
    op.drop_column("product_updates", "published_via")
    op.drop_column("product_updates", "created_by_user_id")
    op.drop_column("product_updates", "dedupe_key")
