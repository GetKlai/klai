"""Fix vexa_meetings RLS: add DELETE policy, allow bot_poller SELECT

Missing DELETE policy caused all meeting deletions to silently match 0 rows.
SELECT policy was tenant-only, blocking the bot_poller background task that
runs without tenant context (same pattern as the existing UPDATE policy).

Revision ID: a1b2c3d4e5f6
Revises: z2a3b4c5d6e7
Create Date: 2026-04-08
"""

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None

_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"
_T_IS_NULL = "NULLIF(current_setting('app.current_org_id', true), '') IS NULL"


def upgrade() -> None:
    # Add missing DELETE policy (scoped to tenant)
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_delete ON vexa_meetings FOR DELETE USING (org_id = {_T})"
    )

    # Update SELECT policy to allow bot_poller (no tenant context),
    # matching the existing UPDATE policy pattern.
    op.execute("DROP POLICY IF EXISTS tenant_read ON vexa_meetings")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_read ON vexa_meetings FOR SELECT USING (org_id = {_T} OR {_T_IS_NULL})"
    )


def downgrade() -> None:
    # Restore original SELECT policy (tenant-only)
    op.execute("DROP POLICY IF EXISTS tenant_read ON vexa_meetings")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_read ON vexa_meetings FOR SELECT USING (org_id = {_T})"
    )

    # Remove DELETE policy
    op.execute("DROP POLICY IF EXISTS tenant_delete ON vexa_meetings")
