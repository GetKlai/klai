"""per-org widget_messages retention override

Revision ID: 63e87b89aa64
Revises: d8b3f6a1c4e9
Create Date: 2026-09-15

Voys keeps widget conversations 90 days so customers can be contacted after
the fact; every other tenant stays on the 7-day global default set in
``settings.widget_messages_retention_days`` (lowered from 90 on 2026-09-11,
SPEC-CHAT-QUALITY-LOOP-001 open item #2). NULL means "use the global
default" — Klai staff opt a tenant into a longer window via
``PATCH /api/admin/orgs/{slug}/widget-retention``, they do not set the
default itself per row. ``portal_orgs`` is portal_api-owned (same precedent
as migration 5d8cef52b18c), so the ADD COLUMN + CHECK run directly in
``upgrade()`` instead of a post-deploy superuser script.
"""

from alembic import op
import sqlalchemy as sa

revision = "63e87b89aa64"
down_revision = "997e0b66f750"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portal_orgs",
        sa.Column("widget_messages_retention_days", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_portal_orgs_widget_retention_days",
        "portal_orgs",
        "widget_messages_retention_days IS NULL OR widget_messages_retention_days BETWEEN 1 AND 365",
    )


def downgrade() -> None:
    op.drop_constraint("ck_portal_orgs_widget_retention_days", "portal_orgs", type_="check")
    op.drop_column("portal_orgs", "widget_messages_retention_days")
