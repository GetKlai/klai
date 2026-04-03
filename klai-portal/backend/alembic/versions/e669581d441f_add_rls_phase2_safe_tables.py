"""Add RLS phase 2: safe tables (SPEC-SEC-003 Fase 1)

Five tables with direct or indirect org_id that have no background-task
write paths. Standard tenant_isolation policies.

Revision ID: e669581d441f
Revises: 7a23f23c419d
Create Date: 2026-04-03
"""

# ruff: noqa: S608  -- all SQL in this file is static DDL, not user-controlled input

from alembic import op

revision = "e669581d441f"
down_revision = "7a23f23c419d"
branch_labels = None
depends_on = None

_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"


def _enable_rls(table: str) -> None:
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
    )
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
    )


def _create_org_policy(table: str) -> None:
    """Standard policy for tables with a direct org_id column."""
    op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
        f"CREATE POLICY tenant_isolation ON {table} USING (org_id = {_T})"
    )


def upgrade() -> None:
    # Tables with a direct org_id column
    for table in (
        "portal_kb_tombstones",
        "portal_user_kb_access",
        "portal_retrieval_gaps",
    ):
        _enable_rls(table)
        _create_org_policy(table)

    # Tables with indirect org_id (via kb_id -> portal_knowledge_bases.org_id)
    for table in ("portal_taxonomy_nodes", "portal_taxonomy_proposals"):
        _enable_rls(table)
        op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (kb_id IN (SELECT id FROM portal_knowledge_bases WHERE org_id = {_T}))"
        )


def downgrade() -> None:
    for table in (
        "portal_taxonomy_proposals",
        "portal_taxonomy_nodes",
        "portal_retrieval_gaps",
        "portal_user_kb_access",
        "portal_kb_tombstones",
    ):
        op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
            f"DROP POLICY IF EXISTS tenant_isolation ON {table}"
        )
        op.execute(  # nosemgrep: formatted-sql-query,sqlalchemy-execute-raw-query
            f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"
        )
