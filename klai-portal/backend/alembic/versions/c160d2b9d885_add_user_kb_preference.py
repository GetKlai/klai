"""add user KB preference columns

Revision ID: c160d2b9d885
Revises: z2a3b4c5d6e7
Create Date: 2026-03-27

Four new columns on portal_users to support per-user KB scope control
(KBScopeBar, SPEC-KB-013). All columns have server-side defaults so existing
rows are migrated without a data backfill.

Note (2026-04-21): This revision was originally authored with rev id
`a1b2c3d4e5f6`, which collided with
`a1b2c3d4e5f6_add_portal_org_allowed_domains.py`. Renamed to a proper
randomly-generated hex id to resolve the collision and the resulting
`Revision ... is present more than once` alembic warning. The merge
migration `d1e2f3a4b5c6_add_recording_deleted_to_vexa_meetings.py` was
extended to include this new id so the DAG still has a single head.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c160d2b9d885"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_users",
        sa.Column("kb_retrieval_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "portal_users",
        sa.Column("kb_personal_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "portal_users",
        sa.Column("kb_slugs_filter", postgresql.ARRAY(sa.String(128)), nullable=True),
    )
    op.add_column(
        "portal_users",
        sa.Column("kb_pref_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("portal_users", "kb_pref_version")
    op.drop_column("portal_users", "kb_slugs_filter")
    op.drop_column("portal_users", "kb_personal_enabled")
    op.drop_column("portal_users", "kb_retrieval_enabled")
