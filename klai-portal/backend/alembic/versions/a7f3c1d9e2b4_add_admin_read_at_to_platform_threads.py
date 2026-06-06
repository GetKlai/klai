"""add admin_read_at to platform_message_threads

Lets platform admins mark a thread as read by opening it (not only by replying),
so the unread indicator clears on read. Nullable ADD COLUMN — metadata-only, no
backfill, RLS-safe (no UPDATE/INSERT in upgrade). The table is owned by
portal_api (created via op.create_table in m1n2o3p4q5r6), so ALTER TABLE here is
permitted without a klai-superuser post-deploy step.

Revision ID: a7f3c1d9e2b4
Revises: n2o3p4q5r6s7
Create Date: 2026-06-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7f3c1d9e2b4"
down_revision: Union[str, Sequence[str], None] = "n2o3p4q5r6s7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "platform_message_threads",
        sa.Column("admin_read_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_message_threads", "admin_read_at")
