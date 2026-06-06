"""add sender_display_name to platform_messages

Stores a point-in-time snapshot of the sender's display name on each message, so
the account side can show the individual Klai team member who sent a message
(instead of a generic "Klai team") without a cross-org live lookup. Like the
"From" line of an email, it must survive renames/offboarding.

Nullable ADD COLUMN — metadata-only, no backfill, RLS-safe. platform_messages is
portal_api-owned (created via op.create_table in m1n2o3p4q5r6), so ALTER TABLE is
permitted without a klai-superuser post-deploy step.

Revision ID: b8e2d4f6a1c3
Revises: p1r2o3d4u5p6
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8e2d4f6a1c3"
down_revision: Union[str, Sequence[str], None] = "p1r2o3d4u5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "platform_messages",
        sa.Column("sender_display_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_messages", "sender_display_name")
