"""Per-org daily budget for version-only graph refreshes.

A graph-extraction version bump makes every active document stale at once;
this counter lets graph_refresh.py enqueue at most GRAPH_REFRESH_DAILY_CAP of
those refreshes per org per UTC day.

Revision ID: a6d41f9e2c73
Revises: e7b3c6a10f42
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a6d41f9e2c73"
down_revision: str | None = "e7b3c6a10f42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE knowledge.graph_refresh_budget (
          org_id text NOT NULL,
          day date NOT NULL,
          enqueued integer NOT NULL CHECK (enqueued >= 0),
          PRIMARY KEY (org_id, day)
        )
        """
    )
    op.execute("ALTER TABLE knowledge.graph_refresh_budget ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge.graph_refresh_budget FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.graph_refresh_budget
          AS RESTRICTIVE
          USING (org_id = knowledge._rls_current_org_id())
          WITH CHECK (org_id = knowledge._rls_current_org_id())
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge.graph_refresh_budget")
