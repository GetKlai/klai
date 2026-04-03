"""fix audit_log RLS: allow inserts without tenant context

The original tenant_isolation policy (migration d7e8f9a0b1c2) uses a single
USING clause for cmd=ALL. PostgreSQL reuses USING as WITH CHECK for INSERT
when no explicit WITH CHECK is provided. This means every INSERT must satisfy:

    org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer

This fails for:
  - Failed logins (org_id=0, set_tenant never called -> setting is empty)
  - Successful logins (set_tenant not yet called at audit write time)
  - Logout events (no authenticated session -> no tenant context)

Fix: Split the single ALL policy into two separate policies:
  1. tenant_isolation_read  (SELECT) - tenants can only read their own audit logs
  2. tenant_isolation_write (INSERT) - all inserts are allowed (append-only table
     already protected by no_update_audit and no_delete_audit RULEs)

Revision ID: 83a82cc61aee
Revises: e3f4a5b6c7d8
Create Date: 2026-04-02
"""

from alembic import op
from sqlalchemy import text

revision = "83a82cc61aee"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None

_TENANT_EXPR = "NULLIF(current_setting('app.current_org_id', true), '')::int"


def upgrade() -> None:
    # Drop the original ALL policy that blocks inserts
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation ON portal_audit_log"))

    # SELECT: tenants can only read their own org's audit entries
    _read_policy = f"CREATE POLICY tenant_isolation_read ON portal_audit_log FOR SELECT USING (org_id = {_TENANT_EXPR})"
    op.execute(text(_read_policy))

    # INSERT: always allow (audit log is append-only, protected by RULEs)
    op.execute(text("CREATE POLICY tenant_isolation_write ON portal_audit_log FOR INSERT WITH CHECK (true)"))


def downgrade() -> None:
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation_write ON portal_audit_log"))
    op.execute(text("DROP POLICY IF EXISTS tenant_isolation_read ON portal_audit_log"))

    # Restore original ALL policy
    _restore_policy = f"CREATE POLICY tenant_isolation ON portal_audit_log USING (org_id = {_TENANT_EXPR})"
    op.execute(text(_restore_policy))
