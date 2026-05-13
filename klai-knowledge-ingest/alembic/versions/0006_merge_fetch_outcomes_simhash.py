"""Merge SPEC-INGEST-RECONCILE-001 (a8c5e1d2f3b4) and SPEC-INGEST-LOGIN-WALL-DETECT-002 (7f2e8a1c5b4d).

Both 0005 migrations branched from ``603787256fb8`` in parallel PRs that
landed within minutes of each other (PR #440 + PR #441). On their own
each migration is correct; alembic refuses ``upgrade head`` because two
heads exist.

This is a no-op merge — both predecessor migrations are independent
schema additions (``knowledge.crawl_jobs.fetch_outcomes`` jsonb vs
``knowledge.crawled_pages.content_simhash`` bigint + index). No
upgrade/downgrade body is needed beyond declaring the merge ancestry.

Revision ID: c1d4e7f2a8b6
Revises: a8c5e1d2f3b4, 7f2e8a1c5b4d
Create Date: 2026-05-06
SPEC: SPEC-INGEST-RECONCILE-001 (alembic-heads recovery)
"""

from collections.abc import Sequence

revision: str = "c1d4e7f2a8b6"
down_revision: tuple[str, ...] = ("a8c5e1d2f3b4", "7f2e8a1c5b4d")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
