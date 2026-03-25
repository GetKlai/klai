"""Add PostgreSQL Row Level Security policies for tenant isolation.

Also adds missing columns to portal_knowledge_bases and portal_group_kb_access
that were absent from the previous migration but present in the ORM models.

Revision ID: a1b2c3d4e5f6
Revises: z2a3b4c5d6e7
Create Date: 2026-03-25
"""
# ruff: noqa: S608  -- all SQL in this file is static DDL, not user-controlled input

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "z2a3b4c5d6e7"
branch_labels = None
depends_on = None

# Tenant context expression — inlined as a literal so all SQL strings use pure
# implicit concatenation (no + operator) which ruff format handles cleanly.
_T = "NULLIF(current_setting('app.current_org_id', true), '')::int"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _create_org_policy(table: str) -> None:
    """Standard policy for tables with a direct org_id column."""
    op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (org_id = {_T})")


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Missing columns from z2a3b4c5d6e7 migration
    # ------------------------------------------------------------------
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("visibility", sa.Text(), nullable=False, server_default="internal"),
    )
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("docs_enabled", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("gitea_repo_slug", sa.Text(), nullable=True),
    )
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("owner_type", sa.Text(), nullable=False, server_default="org"),
    )
    op.add_column(
        "portal_knowledge_bases",
        sa.Column("owner_user_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "portal_group_kb_access",
        sa.Column("role", sa.Text(), nullable=False, server_default="viewer"),
    )

    # ------------------------------------------------------------------
    # 2. RLS — tables with a direct org_id column
    # ------------------------------------------------------------------
    for table in (
        "portal_groups",
        "portal_knowledge_bases",
        "portal_docs_libraries",
        "portal_group_products",
    ):
        _enable_rls(table)
        _create_org_policy(table)

    # ------------------------------------------------------------------
    # 3. RLS — junction tables (no direct org_id; resolved via parent)
    # ------------------------------------------------------------------

    # portal_group_memberships: scope through portal_groups
    _enable_rls("portal_group_memberships")
    op.execute(
        f"CREATE POLICY tenant_isolation ON portal_group_memberships "
        f"USING (group_id IN (SELECT id FROM portal_groups WHERE org_id = {_T}))"
    )

    # portal_group_kb_access: scope through portal_knowledge_bases
    _enable_rls("portal_group_kb_access")
    op.execute(
        f"CREATE POLICY tenant_isolation ON portal_group_kb_access "
        f"USING (kb_id IN (SELECT id FROM portal_knowledge_bases WHERE org_id = {_T}))"
    )

    # portal_group_docs_access: scope through portal_docs_libraries
    _enable_rls("portal_group_docs_access")
    op.execute(
        f"CREATE POLICY tenant_isolation ON portal_group_docs_access "
        f"USING (library_id IN (SELECT id FROM portal_docs_libraries WHERE org_id = {_T}))"
    )


def downgrade() -> None:
    # Remove RLS from junction tables
    for table in (
        "portal_group_docs_access",
        "portal_group_kb_access",
        "portal_group_memberships",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Remove RLS from direct org_id tables
    for table in (
        "portal_group_products",
        "portal_docs_libraries",
        "portal_knowledge_bases",
        "portal_groups",
    ):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Remove added columns
    op.drop_column("portal_group_kb_access", "role")
    op.drop_column("portal_knowledge_bases", "owner_user_id")
    op.drop_column("portal_knowledge_bases", "owner_type")
    op.drop_column("portal_knowledge_bases", "gitea_repo_slug")
    op.drop_column("portal_knowledge_bases", "docs_enabled")
    op.drop_column("portal_knowledge_bases", "visibility")
