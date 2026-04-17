"""add rule_type to portal_rules

Revision ID: 125e31c9e42b
Revises: 49c788860eb3
Create Date: 2026-04-16

Adds `rule_type` column to portal_rules so the API / guardrail layer can
distinguish between plain instruction rules and structured guardrails
(PII block/redact, keyword block/redact). Allowed values are enforced in
the API layer, not by a DB CHECK constraint, so new types can be added
without migrations.
"""

import sqlalchemy as sa
from alembic import op

revision = "125e31c9e42b"
down_revision = "49c788860eb3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_rules",
        sa.Column("rule_type", sa.String(32), nullable=False, server_default="instruction"),
    )


def downgrade() -> None:
    op.drop_column("portal_rules", "rule_type")
