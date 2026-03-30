"""add ical_uid to vexa_meetings

Revision ID: w3x4y5z6a7b8
Revises: v2w3x4y5z6a7
Create Date: 2026-03-24 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "w3x4y5z6a7b8"
down_revision: str = "v2w3x4y5z6a7"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("vexa_meetings", sa.Column("ical_uid", sa.String(512), nullable=True))
    op.create_index("ix_vexa_meetings_ical_uid", "vexa_meetings", ["ical_uid"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_vexa_meetings_ical_uid", table_name="vexa_meetings")
    op.drop_column("vexa_meetings", "ical_uid")
