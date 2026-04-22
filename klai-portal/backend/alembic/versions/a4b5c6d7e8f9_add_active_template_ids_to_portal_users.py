"""add active_template_ids to portal_users

Revision ID: a4b5c6d7e8f9
Revises: 125e31c9e42b
Create Date: 2026-04-22

Adds `active_template_ids INTEGER[]` to portal_users so the LiteLLM hook can
resolve a user's currently active prompt templates and prepend them as a system
message on every chat request. Nullable; null means no templates active.

The API layer (app/api/app_account.py) already exposes this field on the
KB preference endpoint; the model declaration (app/models/portal.py) already
lists the mapped column. This migration brings the database schema in sync.
"""

import sqlalchemy as sa
from alembic import op

revision = "a4b5c6d7e8f9"
down_revision = "125e31c9e42b"
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
