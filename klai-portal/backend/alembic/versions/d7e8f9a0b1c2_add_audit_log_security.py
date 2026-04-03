"""Add append-only enforcement and RLS to portal_audit_log (NEN 7510 SPEC-SEC-001)

Revision ID: d7e8f9a0b1c2
Revises: e8f9a0b1c2d3
Create Date: 2026-03-27
"""

from alembic import op
from sqlalchemy import text

revision = "d7e8f9a0b1c2"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = "v2w3x4y5z6a7"  # depends on add_audit_log migration

_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"


def upgrade() -> None:
    # Fix 4: Append-only enforcement via PostgreSQL RULEs
    op.execute(text("CREATE RULE no_update_audit AS ON UPDATE TO portal_audit_log DO INSTEAD NOTHING"))
    op.execute(text("CREATE RULE no_delete_audit AS ON DELETE TO portal_audit_log DO INSTEAD NOTHING"))

    # Fix 5: Row Level Security for tenant isolation
    op.execute(text("ALTER TABLE portal_audit_log ENABLE ROW LEVEL SECURITY"))
    op.execute(text("ALTER TABLE portal_audit_log FORCE ROW LEVEL SECURITY"))
    _policy = f"CREATE POLICY tenant_isolation ON portal_audit_log USING (org_id = {_T})"
    op.execute(text(_policy))


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON portal_audit_log"))
    op.execute(text("ALTER TABLE portal_audit_log DISABLE ROW LEVEL SECURITY"))
    op.execute(text("DROP RULE IF EXISTS no_delete_audit ON portal_audit_log"))
    op.execute(text("DROP RULE IF EXISTS no_update_audit ON portal_audit_log"))
