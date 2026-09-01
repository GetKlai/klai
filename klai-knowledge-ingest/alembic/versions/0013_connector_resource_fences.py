"""SPEC-CONNECTOR-CANCEL-001 — connector generation fences.

Revision ID: e7b3c6a10f42
Revises: c4a11d9b7e20
Create Date: 2026-09-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e7b3c6a10f42"
down_revision: str | None = "c4a11d9b7e20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge.connector_resource_fences (
          org_id text NOT NULL,
          kb_slug text NOT NULL,
          connector_id text NOT NULL,
          current_generation text NOT NULL,
          state text NOT NULL CHECK (state IN ('active', 'deleting')),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (org_id, kb_slug, connector_id)
        )
        """
    )
    op.execute("ALTER TABLE knowledge.connector_resource_fences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge.connector_resource_fences FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.connector_resource_fences
          AS RESTRICTIVE
          USING (org_id = knowledge._rls_current_org_id())
          WITH CHECK (org_id = knowledge._rls_current_org_id())
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge.connector_resource_fences")
