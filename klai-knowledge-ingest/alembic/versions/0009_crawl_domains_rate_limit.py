"""Add rate_limit column to knowledge.crawl_domains

2026-08-17 (intermedia.com + support.ascendcloud.com incident): stores a
lowered per-domain rate_limit (requests/second) after a crawl hits
RATE_LIMITED or BLOCKED_ANTI_BOT, so the NEXT crawl of that domain starts
already paced down instead of walking into the same wall again. See
knowledge_ingest.domain_selectors.get_domain_rate_limit /
lower_domain_rate_limit and knowledge_ingest.adapters.crawler.run_crawl_job.

``css_selector`` and ``selector_source`` are relaxed to nullable in the same
migration: a rate-limit-only row (a domain that never had a selector
recorded) needs to be representable without inventing a placeholder
selector value. Widening a NOT NULL constraint is safe for every existing
caller — they always supply both values (see
domain_selectors.upsert_domain_selector) — and the CHECK constraint on
selector_source already tolerates NULL (Postgres does not evaluate CHECK
against NULL columns).

Pure DDL, no backfill: existing rows keep their non-NULL css_selector /
selector_source; rate_limit is NULL until a crawl actually lowers it.

Revision ID: a3c9e9286990
Revises: b7c2d9e4f1a3
Create Date: 2026-08-17 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c9e9286990"
down_revision: str = "b7c2d9e4f1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge.crawl_domains
        ADD COLUMN IF NOT EXISTS rate_limit double precision
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_domains
        ALTER COLUMN css_selector DROP NOT NULL
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_domains
        ALTER COLUMN selector_source DROP NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge.crawl_domains DROP COLUMN IF EXISTS rate_limit")
    # NOT NULL is intentionally not restored on downgrade: a rate-limit-only
    # row (css_selector/selector_source NULL) may already exist by the time
    # a downgrade runs, and re-adding NOT NULL would fail on that data.
