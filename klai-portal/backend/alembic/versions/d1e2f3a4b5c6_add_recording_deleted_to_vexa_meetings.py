"""add recording_deleted fields to vexa_meetings

Revision ID: d1e2f3a4b5c6
Revises: 23c5c8b48669, a2b3c4d5e6f7, b4c5d6e7f8g9, b5c6d7e8f9a0, c4d5e6f7a8b9, c160d2b9d885
Create Date: 2026-03-28

2026-04-21: extended with c160d2b9d885 (renamed from duplicate a1b2c3d4e5f6
for portal_users KB preference columns, SPEC-KB-013) so the DAG has a single
head after collision cleanup.
2026-04-22: `a1b2c3d4e5f6` replaced with `23c5c8b48669` after the
portal_org_allowed_domains migration was renamed to a proper random hex.
"""

from alembic import op
import sqlalchemy as sa

revision = "d1e2f3a4b5c6"
down_revision = (
    "23c5c8b48669",  # was a1b2c3d4e5f6 (add_portal_org_allowed_domains), renamed 2026-04-22
    "a2b3c4d5e6f7",
    "b4c5d6e7f8g9",
    "b5c6d7e8f9a0",
    "c4d5e6f7a8b9",
    "c160d2b9d885",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vexa_meetings",
        sa.Column("recording_deleted", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "vexa_meetings",
        sa.Column("recording_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vexa_meetings", "recording_deleted_at")
    op.drop_column("vexa_meetings", "recording_deleted")
