"""SPEC-INGEST-LOGIN-WALL-DETECT-001 — add error_summary + failed_partial.

Two changes to ``knowledge.crawl_jobs`` to support REQ-04 (BFS-continuity for
anonymous-crawl auth walls):

1. ADD COLUMN ``error_summary jsonb NULL`` — populated by ``run_crawl_job``
   after BFS completes when ``len(auth_wall_pages) > 0``. Schema:
       {"login_walls_skipped": int, "sample_urls": [str up to 10]}
   The ``error`` column stays for the existing ``AuthWallDetected`` (cookie-
   path) flow which writes a single short string. The two columns are
   complementary: ``error`` is "the job failed because X", ``error_summary``
   is "the job ran to completion but here's a per-page-issue tally".

2. EXTEND the status CHECK constraint to allow ``failed_partial``. New
   semantics: ``succeeded`` (>= 1 page ingested), ``failed`` (catastrophic
   error or AuthWallDetected halt), ``failed_partial`` (0 pages ingested
   AND >= 1 anonymous-wall skipped). The existing ``pending``/``running``/
   ``completed``/``failed`` values remain valid; ``completed`` is the legacy
   alias used by current writers — kept for backwards compatibility during
   rollout.

Idempotent: ADD COLUMN uses IF NOT EXISTS; the CHECK swap drops by name and
re-adds, both wrapped in DO blocks to skip when the new constraint already
exists.

Revision ID: 603787256fb8
Revises: 9a3c4d5e6f7b
Create Date: 2026-05-06
SPEC: SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "603787256fb8"
down_revision: str | None = "9a3c4d5e6f7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge.crawl_jobs ADD COLUMN IF NOT EXISTS error_summary jsonb")

    # Drop the old CHECK constraint and re-add with failed_partial.
    # Wrapped in DO blocks so re-running on a partially-migrated DB is safe.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'crawl_jobs_status_check'
                  AND conrelid = 'knowledge.crawl_jobs'::regclass
            ) THEN
                ALTER TABLE knowledge.crawl_jobs
                DROP CONSTRAINT crawl_jobs_status_check;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_jobs
        ADD CONSTRAINT crawl_jobs_status_check
        CHECK (status = ANY (ARRAY[
            'pending'::text,
            'running'::text,
            'completed'::text,
            'failed'::text,
            'failed_partial'::text
        ]))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'crawl_jobs_status_check'
                  AND conrelid = 'knowledge.crawl_jobs'::regclass
            ) THEN
                ALTER TABLE knowledge.crawl_jobs
                DROP CONSTRAINT crawl_jobs_status_check;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_jobs
        ADD CONSTRAINT crawl_jobs_status_check
        CHECK (status = ANY (ARRAY[
            'pending'::text,
            'running'::text,
            'completed'::text,
            'failed'::text
        ]))
        """
    )
    op.execute("ALTER TABLE knowledge.crawl_jobs DROP COLUMN IF EXISTS error_summary")
