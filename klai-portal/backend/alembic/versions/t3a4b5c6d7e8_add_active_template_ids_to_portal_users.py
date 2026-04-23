"""Add active_template_ids to portal_users (SPEC-CHAT-TEMPLATES-001)

Revision ID: t3a4b5c6d7e8
Revises: t2a3b4c5d6e7
Create Date: 2026-04-23

Adds `active_template_ids INTEGER[] NULL` to portal_users so the LiteLLM
pre-call hook can resolve which prompt templates a user has toggled on.
NULL means no active templates.

The column is NOT a foreign key array (PostgreSQL doesn't support array
FKs directly). Referential integrity is enforced at the application
layer: `PATCH /api/app/account/kb-preference` validates every ID against
`portal_templates.org_id = caller.org.id`. Dangling IDs (template
deleted after activation) are silently skipped by
`/internal/templates/effective` — that's the expected behaviour per
REQ-TEMPLATES-INTERNAL-E5.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "t3a4b5c6d7e8"
down_revision = "t2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_users",
        sa.Column(
            "active_template_ids",
            sa.ARRAY(sa.Integer()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("portal_users", "active_template_ids")
