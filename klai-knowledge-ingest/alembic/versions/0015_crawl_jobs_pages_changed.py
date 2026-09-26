"""Add knowledge.crawl_jobs.pages_changed.

pages_done counts every page whose ingest completed, including pages skipped
as unchanged, so a caller cannot tell a crawl that changed the knowledge base
from one that did not. klai-connector forwards this count to the portal, which
reanalyses support cases (several LLM calls per case) only after a change.

The column is added without a default, so every existing job keeps NULL and
reports "unknown"; the default of 0 is set afterwards and applies to new jobs
only. The crawler increments it per changed page and per retired stale page,
and ``pages_changed + n`` stays NULL on an old job that is recovered.

Revision ID: 5b1d8f2c9a07
Revises: a6d41f9e2c73
Create Date: 2026-09-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "5b1d8f2c9a07"
down_revision: str | None = "a6d41f9e2c73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge.crawl_jobs ADD COLUMN IF NOT EXISTS pages_changed integer")
    op.execute("ALTER TABLE knowledge.crawl_jobs ALTER COLUMN pages_changed SET DEFAULT 0")


def downgrade() -> None:
    op.execute("ALTER TABLE knowledge.crawl_jobs DROP COLUMN IF EXISTS pages_changed")
