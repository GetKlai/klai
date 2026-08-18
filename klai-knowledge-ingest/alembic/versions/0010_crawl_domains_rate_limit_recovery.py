"""Add rate-limit recovery counters to knowledge.crawl_domains

2026-08-18 (block B, follow-up to 0009_crawl_domains_rate_limit):
``lower_domain_rate_limit`` only ever halved a domain's rate limit —
nothing ever raised it back up, so a domain that had one bad crawl stayed
throttled forever. This adds the two columns the additive-recovery
regelwet needs to track state between crawls:

- ``clean_streak``: consecutive clean (SUCCESS) observations accumulated
  since the last congestion signal (or since the override was created).
- ``last_congestion_at``: when the domain last hit RATE_LIMITED or
  BLOCKED_ANTI_BOT — the hysteresis cooldown is measured from this.

See knowledge_ingest.domain_rate_limit_control.compute_domain_rate_limit_update
(the pure regelwet) and knowledge_ingest.domain_selectors.
get_domain_rate_limit_state / save_domain_rate_limit_state (persistence).

Ownership note (unlike some other knowledge.* tables): ``knowledge.
crawl_domains`` is owned by ``klai``, and knowledge-ingest connects as
``klai`` itself (see 0009's docstring precedent + this migration's
DDL-only shape) — so this migration runs as the table owner and needs no
post-deploy SQL step.

Both columns get a default (0 / NULL) so ``ALTER TABLE ADD COLUMN`` is a
metadata-only operation on PostgreSQL 11+: no row rewrite, and therefore
nothing that could collide with the RLS WITH CHECK on this table. No
``UPDATE`` runs in this migration.

Revision ID: d0fb00b16473
Revises: a3c9e9286990
Create Date: 2026-08-18 10:36:48.634580

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0fb00b16473"
down_revision: str | Sequence[str] | None = "a3c9e9286990"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge.crawl_domains
        ADD COLUMN IF NOT EXISTS clean_streak integer NOT NULL DEFAULT 0
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_domains
        ADD COLUMN IF NOT EXISTS last_congestion_at timestamp with time zone
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge.crawl_domains DROP COLUMN IF EXISTS clean_streak")
    op.execute("ALTER TABLE knowledge.crawl_domains DROP COLUMN IF EXISTS last_congestion_at")
