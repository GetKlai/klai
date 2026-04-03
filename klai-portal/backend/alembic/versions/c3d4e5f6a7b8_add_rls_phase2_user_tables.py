"""Add RLS phase 2: user and connector tables (SPEC-SEC-003 Fase 2b)

portal_users and portal_connectors use a permissive policy when no tenant
context is set, because internal service endpoints need to look up a resource
by ID to discover the org_id before calling set_tenant().

portal_user_products uses a strict policy because set_tenant() is always
called before any query on this table.

IMPORTANT: The code fixes in internal.py (adding set_tenant to internal
endpoints) MUST be deployed BEFORE this migration is applied.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-03
"""

from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
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
    # portal_users: permissive when no tenant context (internal endpoint lookup)
    _enable_rls("portal_users")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_isolation ON portal_users USING (org_id = {_T} OR {_T_IS_NULL})"
    )

    # portal_user_products: strict (set_tenant always called before query)
    _enable_rls("portal_user_products")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_isolation ON portal_user_products USING (org_id = {_T})"
    )

    # portal_connectors: permissive when no tenant context (internal endpoint lookup)
    _enable_rls("portal_connectors")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_isolation ON portal_connectors USING (org_id = {_T} OR {_T_IS_NULL})"
    )


def downgrade() -> None:
    for table in (
        "portal_connectors",
        "portal_user_products",
        "portal_users",
    ):
        op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
            f"DROP POLICY IF EXISTS tenant_isolation ON {table}"
        )
        op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
            f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"
        )
