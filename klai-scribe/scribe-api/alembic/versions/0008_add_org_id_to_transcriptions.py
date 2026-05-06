"""add org_id to scribe.transcriptions for tenant isolation

Revision ID: 0008_add_org_id_ti010a
Revises: 0007_c5f9e3a4
Create Date: 2026-05-05 00:00:00.000000

SPEC-TI-010A Finding A-9.

Adds ``org_id`` (Zitadel org resource-owner ID) to ``scribe.transcriptions``
so that the table can enforce per-tenant isolation in addition to per-user
scoping.  Without ``org_id`` a user account that migrates from org A to org B
retains access to org A transcripts via ``user_id`` (Zitadel sub) alone.

Column is ``NOT NULL DEFAULT ''`` -- the empty string acts as a sentinel for
historical rows.  The post-deploy SQL file
``post_deploy_0008_add_org_id_ti010a_rls.sql`` adds a Cat-D RLS policy that
reads ``scribe._rls_current_org_id()``.  Because there are no production
users yet (scribe is in limited-preview), no backfill is required and the
empty-string sentinel is acceptable for the rollout window.

Down-revision removes the column and index.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_add_org_id_ti010a"
down_revision: str | Sequence[str] | None = "0007_c5f9e3a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transcriptions",
        sa.Column("org_id", sa.VARCHAR(255), nullable=False, server_default=""),
        schema="scribe",
    )
    op.create_index(
        "ix_transcriptions_org_user",
        "transcriptions",
        ["org_id", "user_id"],
        schema="scribe",
    )


def downgrade() -> None:
    op.drop_index("ix_transcriptions_org_user", table_name="transcriptions", schema="scribe")
    op.drop_column("transcriptions", "org_id", schema="scribe")
