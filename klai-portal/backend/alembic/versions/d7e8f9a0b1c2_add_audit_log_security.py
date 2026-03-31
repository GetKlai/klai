"""Add append-only enforcement and RLS to portal_audit_log (NEN 7510 SPEC-SEC-001)

Revision ID: d7e8f9a0b1c2
Revises: e8f9a0b1c2d3
Create Date: 2026-03-27
"""

from alembic import op

revision = "d7e8f9a0b1c2"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = "v2w3x4y5z6a7"  # depends on add_audit_log migration

_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"


def upgrade() -> None:
    # Fix 4: Append-only enforcement via PostgreSQL RULEs
    op.execute("CREATE RULE no_update_audit AS ON UPDATE TO portal_audit_log DO INSTEAD NOTHING")
    op.execute("CREATE RULE no_delete_audit AS ON DELETE TO portal_audit_log DO INSTEAD NOTHING")

    # Fix 5: Row Level Security for tenant isolation
    op.execute("ALTER TABLE portal_audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE portal_audit_log FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON portal_audit_log USING (org_id = {_T})"
    )  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON portal_audit_log")
    op.execute("ALTER TABLE portal_audit_log DISABLE ROW LEVEL SECURITY")
    op.execute("DROP RULE IF EXISTS no_delete_audit ON portal_audit_log")
    op.execute("DROP RULE IF EXISTS no_update_audit ON portal_audit_log")
