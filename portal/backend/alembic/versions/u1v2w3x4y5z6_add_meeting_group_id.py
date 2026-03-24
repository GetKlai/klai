"""add group_id to vexa_meetings

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-03-24

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "u1v2w3x4y5z6"
down_revision = "t0u1v2w3x4y5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vexa_meetings",
        sa.Column(
            "group_id",
            sa.Integer(),
            sa.ForeignKey("portal_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_vexa_meetings_group_id", "vexa_meetings", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_vexa_meetings_group_id", table_name="vexa_meetings")
    op.drop_column("vexa_meetings", "group_id")
