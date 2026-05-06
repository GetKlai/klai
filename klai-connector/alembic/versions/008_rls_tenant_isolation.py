"""Enable Row Level Security on connector.connectors and connector.sync_runs.

SPEC-TI-002 / audit finding A-7 (tenant-isolation audit 2026-05-05).

Background
----------
The connector schema has two tenant-bearing tables:
  - connector.connectors  (org_id VARCHAR(255))
  - connector.sync_runs   (org_id VARCHAR(255), nullable — legacy rows)

Both had ZERO RLS protection. Application-level WHERE org_id = :org_id
filters were the only isolation layer — one refactor away from a silent
cross-tenant data leak.

This migration enables ENABLE + FORCE ROW LEVEL SECURITY on both tables.
It does NOT create the RLS policies; policies (and the helper function
_rls_current_org_text()) require CREATE FUNCTION / CREATE POLICY privileges
that belong to the ``klai`` superuser role, NOT to the ``connector_api``
role that alembic runs as. Those DDL statements live in the companion
post-deploy SQL file:

    klai-connector/alembic/versions/post_deploy_008_rls_tenant_isolation.sql

Operator must apply that file as ``klai`` superuser AFTER this migration
completes (see SPEC-TI-002 operator-step).

FORCE ROW LEVEL SECURITY
-------------------------
FORCE RLS makes the policy apply to the table owner as well. This is
defence-in-depth: even if a code path runs as the klai superuser or the
table-owner role, it still sees the same tenant-scoped view.

Ordering note
-------------
ENABLE must precede the policy creation in post_deploy_008. The DB
accepts CREATE POLICY on a table even before ENABLE is set, but the
policy only fires once ENABLE is present. We ENABLE here (as connector_api
can do it) and the policies are created by klai in post_deploy.

Revision ID: 008_rls_tenant_isolation
Revises: 007_sync_runs_fk
Create Date: 2026-05-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "008_rls_tenant_isolation"
down_revision: str | None = "007_sync_runs_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ENABLE + FORCE on connector.connectors
    op.execute("ALTER TABLE connector.connectors ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connector.connectors FORCE ROW LEVEL SECURITY")

    # ENABLE + FORCE on connector.sync_runs
    op.execute("ALTER TABLE connector.sync_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connector.sync_runs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Disable RLS (policies are not dropped here; operator must drop them
    # manually via post_deploy_008 rollback if needed).
    op.execute("ALTER TABLE connector.sync_runs NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connector.sync_runs DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connector.connectors NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE connector.connectors DISABLE ROW LEVEL SECURITY")
