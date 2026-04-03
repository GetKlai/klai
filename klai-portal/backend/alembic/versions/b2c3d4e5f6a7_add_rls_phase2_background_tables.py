"""Add RLS phase 2: background task tables (SPEC-SEC-003 Fase 2a)

Split policies for tables with background-task write paths that operate
without tenant context. Pattern: permissive INSERT, scoped SELECT.
vexa_meetings also needs a permissive UPDATE for background poller.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-03
"""

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"
_T_IS_NULL = "NULLIF(current_setting('app.current_org_id', true), '') IS NULL"


def _enable_rls(table: str) -> None:
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
    )
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
    )


def upgrade() -> None:
    # -- product_events: INSERT from emit_event() without tenant context --
    _enable_rls("product_events")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_read ON product_events FOR SELECT USING (org_id = {_T})"
    )
    op.execute("CREATE POLICY tenant_write ON product_events FOR INSERT WITH CHECK (true)")

    # -- vexa_meetings: INSERT + UPDATE from background tasks without tenant context --
    _enable_rls("vexa_meetings")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_read ON vexa_meetings FOR SELECT USING (org_id = {_T})"
    )
    op.execute("CREATE POLICY tenant_write ON vexa_meetings FOR INSERT WITH CHECK (true)")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_update ON vexa_meetings FOR UPDATE USING (org_id = {_T} OR {_T_IS_NULL})"
    )


def downgrade() -> None:
    # vexa_meetings
    for policy in ("tenant_update", "tenant_write", "tenant_read"):
        op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
            f"DROP POLICY IF EXISTS {policy} ON vexa_meetings"
        )
    op.execute("ALTER TABLE vexa_meetings DISABLE ROW LEVEL SECURITY")

    # product_events
    for policy in ("tenant_write", "tenant_read"):
        op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
            f"DROP POLICY IF EXISTS {policy} ON product_events"
        )
    op.execute("ALTER TABLE product_events DISABLE ROW LEVEL SECURITY")
