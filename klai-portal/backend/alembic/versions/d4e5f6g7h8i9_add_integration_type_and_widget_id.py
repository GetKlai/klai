"""add integration_type and widget_id to partner_api_keys

SPEC-WIDGET-001 Task 1: Add widget support columns.

Revision ID: d4e5f6g7h8i9
Revises: c3d4e5f6g7h8, a9b0c1d2e3f4
Create Date: 2026-04-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4e5f6g7h8i9"
down_revision = ("c3d4e5f6g7h8", "a9b0c1d2e3f4")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add integration_type column: NOT NULL with default 'api'
    op.add_column(
        "partner_api_keys",
        sa.Column(
            "integration_type",
            sa.String(length=10),
            nullable=False,
            server_default="api",
        ),
    )

    # Add widget_id column: nullable, unique
    op.add_column(
        "partner_api_keys",
        sa.Column(
            "widget_id",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_partner_api_keys_widget_id",
        "partner_api_keys",
        ["widget_id"],
    )

    # Add widget_config column: nullable JSONB
    op.add_column(
        "partner_api_keys",
        sa.Column(
            "widget_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # Add check constraint to limit integration_type values
    op.create_check_constraint(
        "ck_partner_api_keys_integration_type",
        "partner_api_keys",
        "integration_type IN ('api', 'widget')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_partner_api_keys_integration_type", "partner_api_keys", type_="check")
    op.drop_column("partner_api_keys", "widget_config")
    op.drop_constraint("uq_partner_api_keys_widget_id", "partner_api_keys", type_="unique")
    op.drop_column("partner_api_keys", "widget_id")
    op.drop_column("partner_api_keys", "integration_type")
