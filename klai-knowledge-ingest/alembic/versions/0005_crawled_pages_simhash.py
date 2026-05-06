"""SPEC-INGEST-LOGIN-WALL-DETECT-002 Phase A -- add content_simhash column.

Adds a 64-bit SimHash fingerprint column on ``knowledge.crawled_pages`` plus a
partial index on ``(org_id, kb_slug, content_simhash)`` for cluster-membership
lookups.

The column is NULL-able to allow additive deployment: existing rows stay NULL
until the Phase D backfill task computes a fingerprint for them, and the
runtime detector treats absent fingerprints as cold-start (returns None
unflagged). The partial index restricts entries to non-NULL rows so the index
size stays proportional to backfilled pages, not to legacy rows.

Idempotent on re-run: both DDL statements use IF NOT EXISTS guards. Downgrade
reverses cleanly (drops index then column, both with IF EXISTS).

Schema isolation: this migration runs against the ``knowledge`` schema, whose
alembic_version table is separate from portal-api's ``public.alembic_version``
and connector's ``connector.alembic_version`` (see ``alembic/env.py``).

Revision ID: 7f2e8a1c5b4d
Revises: 603787256fb8
Create Date: 2026-05-06
SPEC: SPEC-INGEST-LOGIN-WALL-DETECT-002 REQ-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "7f2e8a1c5b4d"
down_revision: str | None = "603787256fb8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge.crawled_pages "
        "ADD COLUMN IF NOT EXISTS content_simhash bigint"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawled_pages_simhash_org_kb "
        "ON knowledge.crawled_pages (org_id, kb_slug, content_simhash) "
        "WHERE content_simhash IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS knowledge.idx_crawled_pages_simhash_org_kb")
    op.execute(
        "ALTER TABLE knowledge.crawled_pages DROP COLUMN IF EXISTS content_simhash"
    )
