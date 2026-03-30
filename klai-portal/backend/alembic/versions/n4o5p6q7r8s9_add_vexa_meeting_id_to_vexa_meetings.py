"""add vexa_meeting_id to vexa_meetings

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-03-23

"""

from alembic import op
import sqlalchemy as sa

revision = "n4o5p6q7r8s9"
down_revision = "m3n4o5p6q7r8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vexa_meetings", sa.Column("vexa_meeting_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("vexa_meetings", "vexa_meeting_id")
