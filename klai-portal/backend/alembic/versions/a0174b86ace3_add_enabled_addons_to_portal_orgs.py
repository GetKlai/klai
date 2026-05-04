"""Add portal_orgs.enabled_addons column.

Revision ID: a0174b86ace3
Revises: 59fff72b480b
Create Date: 2026-05-03

SPEC-PORTAL-PROFILES-001 Phase 2 P2.1.

Adds `enabled_addons text[] NOT NULL DEFAULT '{}'` to portal_orgs.
This column stores the tenant-level add-on toggles (scribe, docs).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "a0174b86ace3"
down_revision = "59fff72b480b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_orgs",
        sa.Column(
            "enabled_addons",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )


def downgrade() -> None:
    op.drop_column("portal_orgs", "enabled_addons")
