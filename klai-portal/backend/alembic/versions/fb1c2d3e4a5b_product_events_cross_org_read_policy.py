"""Marker: allow platform admin cross-org reads on product_events.

The DDL itself runs as klai-superuser in
``post_deploy_fb1c2d3e4a5b_product_events_cross_org_read_policy.sql`` because
``product_events`` has RLS policies and is treated as owner/RLS-sensitive DDL
by the deploy guard.

Revision ID: fb1c2d3e4a5b
Revises: 874c2c370830
Create Date: 2026-05-27
"""

from __future__ import annotations

revision = "fb1c2d3e4a5b"
down_revision = "874c2c370830"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op marker. See sibling post-deploy SQL."""
    pass


def downgrade() -> None:
    """No-op marker."""
    pass
