"""Add knowledge.crawl_url_memory: when each connector address was last tried.

A crawl that cannot visit every known address continues the next night with
what was tried longest ago (knowledge_ingest.crawl_memory). One row per
address and connector, recorded once per accepted crawl. ``kb_slug`` is kept
so a knowledge-base delete can remove the rows.

The crawl frontier checkpoint gains the source kind ``known`` for an address
that is in the frontier because the source remembered it, not because tonight's
sitemap or pages listed it; the CHECK is widened to admit it.

Revision ID: 7c3e9a41d2b6
Revises: 5b1d8f2c9a07
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7c3e9a41d2b6"
down_revision: str | None = "5b1d8f2c9a07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge.crawl_url_memory (
          org_id text NOT NULL,
          kb_slug text NOT NULL,
          connector_id text NOT NULL,
          canonical_url text NOT NULL,
          url text NOT NULL,
          depth integer NOT NULL CHECK (depth >= 0),
          last_visited_at timestamptz,
          PRIMARY KEY (org_id, connector_id, canonical_url)
        )
        """
    )
    op.execute("ALTER TABLE knowledge.crawl_url_memory ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge.crawl_url_memory FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.crawl_url_memory
          AS RESTRICTIVE
          USING (org_id = knowledge._rls_current_org_id())
          WITH CHECK (org_id = knowledge._rls_current_org_id())
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_job_frontier
          DROP CONSTRAINT IF EXISTS crawl_job_frontier_source_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_job_frontier
          ADD CONSTRAINT crawl_job_frontier_source_kind_check
          CHECK (source_kind IN ('start', 'sitemap', 'page_link', 'known'))
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM knowledge.crawl_job_frontier WHERE source_kind = 'known'")
    op.execute(
        """
        ALTER TABLE knowledge.crawl_job_frontier
          DROP CONSTRAINT IF EXISTS crawl_job_frontier_source_kind_check
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_job_frontier
          ADD CONSTRAINT crawl_job_frontier_source_kind_check
          CHECK (source_kind IN ('start', 'sitemap', 'page_link'))
        """
    )
    op.execute("DROP TABLE IF EXISTS knowledge.crawl_url_memory")
