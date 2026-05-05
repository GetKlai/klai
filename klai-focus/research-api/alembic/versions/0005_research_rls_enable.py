"""A-10: Enable and force RLS on all four research-schema tables

The research schema (notebooks, sources, chunks, chat_messages) had zero RLS
as of the 2026-05-05 audit. This migration enables FORCE ROW LEVEL SECURITY
on all four tables (the service role is `klai` superuser so FORCE is needed
to enforce policies even for superuser DML from application code).

The actual policies and the _rls_current_org_id() helper function are
created in the operator-run post_deploy SQL (post_deploy_0005_research_rls.sql)
because alembic runs as a user that cannot CREATE FUNCTION with SECURITY
INVOKER in the public/research schema — only the `klai` superuser can.

Finding: A-10 (audit-tenant-isolation-2026-05-05)
Refs: SPEC-TI-004-RLS-RESEARCH

OPERATOR STEP (run after deploy):
  ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \\
    < klai-focus/research-api/alembic/versions/post_deploy_0005_research_rls.sql
  docker restart klai-core-research-api-1

Revision ID: 0005_research_rls_enable
Revises: 0004_chat_messages_uuid
Create Date: 2026-05-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_research_rls_enable"
down_revision: str | Sequence[str] | None = "0004_chat_messages_uuid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable and force RLS on all four research-schema tenant-tagged tables.
    # Policies are added via post_deploy_0005_research_rls.sql (klai superuser).
    # Without a policy, ENABLE RLS alone makes the table default-deny for all
    # non-owner sessions — until the post_deploy SQL runs, the research-api
    # cannot read any rows. Run the post_deploy SQL immediately after this migration.
    for table in ("notebooks", "sources", "chunks", "chat_messages"):
        op.execute(f"ALTER TABLE research.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE research.{table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in ("notebooks", "sources", "chunks", "chat_messages"):
        op.execute(f"ALTER TABLE research.{table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE research.{table} NO FORCE ROW LEVEL SECURITY")
