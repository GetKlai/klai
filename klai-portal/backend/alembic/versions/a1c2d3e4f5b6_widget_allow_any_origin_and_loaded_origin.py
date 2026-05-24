"""widget allow_any_origin and loaded_origin columns — SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2

Adds:
  - widgets.allow_any_origin BOOLEAN NOT NULL DEFAULT false
    Explicit opt-in flag for open-world origin policy. When True, the
    origin_allowed() gate is bypassed entirely. Replaces the old
    "empty allowed_origins = open to the world" behaviour.
  - widget_conversations.loaded_origin VARCHAR(200) NULL
    Records the Origin header from each conversation start for audit
    visibility. NULL when Origin header was absent (e.g. direct API call).

Both columns are additive DDL — no per-row writes in upgrade() so this
migration is safe on Cat-B (widgets) and Cat-D (widget_conversations)
RLS tables per the rls-with-check-blocks-migration-update pitfall.

Data migration (3-branch per-row UPDATE for existing widgets) lives in
post_deploy_a1c2d3e4f5b6.sql and must be applied as the klai superuser.

Revision ID: a1c2d3e4f5b6
Revises: 5b7c9d1e2f3a
Create Date: 2026-05-24
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c2d3e4f5b6"
down_revision = "5b7c9d1e2f3a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add widgets.allow_any_origin — DDL only, no per-row write.
    op.add_column(
        "widgets",
        sa.Column(
            "allow_any_origin",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # Add widget_conversations.loaded_origin — DDL only, nullable so
    # existing rows stay valid without a backfill.
    op.add_column(
        "widget_conversations",
        sa.Column(
            "loaded_origin",
            sa.String(200),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("widget_conversations", "loaded_origin")
    op.drop_column("widgets", "allow_any_origin")
