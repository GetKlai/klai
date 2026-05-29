"""drop unused feedback item roadmap/tracker fields

These columns were added anticipating an in-flight management product
(external tracker links, public roadmap, target window, owner) that Klai
does not build: the feedback pipeline is report -> triage -> solve outside
-> notify. The columns carried no behaviour (generic passthrough setter +
serializer only), so they are dropped. shipped_at / notification_state /
resolution_* stay — those are used by the resolve/notify flow.

feedback_items is owned by portal_api (its RLS was enabled and policies
created via alembic upgrade() in fa2b3c4d5e6f), so DROP COLUMN is safe to
run inside the entrypoint's `alembic upgrade head` — it is pure metadata
DDL, writes no rows, and fires no RLS WITH CHECK.

Revision ID: f9e8d7c6b5a4
Revises: c8d9e0f1a2b3
Create Date: 2026-05-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f9e8d7c6b5a4"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("feedback_items", "external_tracker_type")
    op.drop_column("feedback_items", "external_tracker_id")
    op.drop_column("feedback_items", "external_tracker_url")
    op.drop_column("feedback_items", "public_feedback_url")
    op.drop_column("feedback_items", "public_title")
    op.drop_column("feedback_items", "public_summary")
    op.drop_column("feedback_items", "target_window")
    op.drop_column("feedback_items", "owner")


def downgrade() -> None:
    op.add_column("feedback_items", sa.Column("owner", sa.String(length=128), nullable=True))
    op.add_column("feedback_items", sa.Column("target_window", sa.String(length=64), nullable=True))
    op.add_column("feedback_items", sa.Column("public_summary", sa.Text(), nullable=True))
    op.add_column("feedback_items", sa.Column("public_title", sa.String(length=256), nullable=True))
    op.add_column("feedback_items", sa.Column("public_feedback_url", sa.String(length=2048), nullable=True))
    op.add_column("feedback_items", sa.Column("external_tracker_url", sa.String(length=2048), nullable=True))
    op.add_column("feedback_items", sa.Column("external_tracker_id", sa.String(length=128), nullable=True))
    op.add_column("feedback_items", sa.Column("external_tracker_type", sa.String(length=32), nullable=True))
