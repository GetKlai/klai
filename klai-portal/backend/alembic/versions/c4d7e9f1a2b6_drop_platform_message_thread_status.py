"""drop status from platform_message_threads

The open/closed lifecycle on message threads added no value (you don't "close" a
chat) and is being removed end-to-end. Dropping the column also removes its
CHECK constraint and the (org_id, status, last_message_at) index; we add back an
(org_id, last_message_at) index for the per-org recency listing.

platform_message_threads is portal_api-owned (created via op.create_table), so
ALTER TABLE here needs no klai-superuser post-deploy step. DROP COLUMN is
metadata-only in PG (no table rewrite).

Revision ID: c4d7e9f1a2b6
Revises: pu20260606b2
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d7e9f1a2b6"
down_revision: Union[str, Sequence[str], None] = "pu20260606b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Dropping the column cascades the dependent index + CHECK constraint.
    op.drop_column("platform_message_threads", "status")
    op.create_index(
        "ix_platform_message_threads_org_last_message",
        "platform_message_threads",
        ["org_id", "last_message_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_platform_message_threads_org_last_message", table_name="platform_message_threads")
    op.add_column(
        "platform_message_threads",
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'open'")),
    )
    op.create_check_constraint(
        "ck_platform_message_threads_status",
        "platform_message_threads",
        "status IN ('open', 'closed')",
    )
    op.create_index(
        "ix_platform_message_threads_org_status",
        "platform_message_threads",
        ["org_id", "status", "last_message_at"],
    )
