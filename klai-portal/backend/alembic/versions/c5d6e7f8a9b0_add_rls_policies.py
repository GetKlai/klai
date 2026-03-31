"""Add PostgreSQL Row Level Security policies for tenant isolation.

Revision ID: c5d6e7f8a9b0
Revises: a3b4c5d6e7f8
Create Date: 2026-03-26
"""
# ruff: noqa: S608  -- all SQL in this file is static DDL, not user-controlled input

from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

# Tenant context expression — inlined as a literal so all SQL strings use pure
# implicit concatenation (no + operator) which ruff format handles cleanly.
_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query


def _create_org_policy(table: str) -> None:
    """Standard policy for tables with a direct org_id column."""
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (org_id = {_T})")  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query


def upgrade() -> None:
    # Tables with a direct org_id column
    for table in (
        "portal_groups",
        "portal_knowledge_bases",
        "portal_group_products",
    ):
        _enable_rls(table)
        _create_org_policy(table)

    # Junction tables (no direct org_id; resolved via parent)
    _enable_rls("portal_group_memberships")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_isolation ON portal_group_memberships "
        f"USING (group_id IN (SELECT id FROM portal_groups WHERE org_id = {_T}))"
    )

    _enable_rls("portal_group_kb_access")
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_isolation ON portal_group_kb_access "
        f"USING (kb_id IN (SELECT id FROM portal_knowledge_bases WHERE org_id = {_T}))"
    )


def downgrade() -> None:
    for table in (
        "portal_group_kb_access",
        "portal_group_memberships",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for table in (
        "portal_group_products",
        "portal_knowledge_bases",
        "portal_groups",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
