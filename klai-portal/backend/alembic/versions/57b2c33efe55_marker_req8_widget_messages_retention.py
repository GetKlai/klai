"""REQ-8 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): widget_messages retention marker.

The DDL change (length CHECK constraint on widget_messages.content) lives in
post_deploy_57b2c33efe55.sql.  widget_messages is owned by the 'klai' superuser
role — ALTER TABLE is not executable by the 'portal_api' role that runs
alembic upgrade head.  The post-deploy SQL is applied manually by an operator
(or via apply_post_deploy_sql.sh) after this migration has been stamped.

Revision ID: 57b2c33efe55
Revises: b2d3e4f5a6c7
Create Date: 2026-05-24
"""

from __future__ import annotations

from alembic import op  # noqa: F401 — imported for future use

# revision identifiers, used by Alembic.
revision = "57b2c33efe55"
down_revision = "b2d3e4f5a6c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: widget_messages is klai-owned; DDL is in post_deploy_57b2c33efe55.sql
    pass


def downgrade() -> None:
    # No-op: post-deploy SQL is not automatically reversed.
    pass
