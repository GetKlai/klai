"""REQ-16 — widget soft-delete + drop CASCADE marker.

The DDL itself runs as klai-superuser in
``post_deploy_874c2c370830_widget_soft_delete.sql`` because
``widgets`` and ``widget_conversations`` are klai-owned tables.

Revision ID: 874c2c370830
Revises: 48fce7e310d6
Create Date: 2026-05-25
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "874c2c370830"
down_revision = "48fce7e310d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """No-op marker. The schema change is applied via the sibling
    post_deploy SQL by the klai superuser.
    """
    # Intentionally empty: see post_deploy_874c2c370830_widget_soft_delete.sql
    pass


def downgrade() -> None:
    """No-op marker."""
    pass
