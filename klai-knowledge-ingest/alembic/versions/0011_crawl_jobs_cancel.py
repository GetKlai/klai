"""crawl-cancel — add knowledge.crawl_jobs.cancel_requested + 'cancelled' status.

The existing ``POST /ingest/v1/crawl/sync/{job_id}/cancel`` endpoint only
signalled Procrastinate's own ``abort_requested`` column — the ``run_crawl``
task never checked it, so a cancel request on an in-flight crawl was a
silent no-op (observed 2026-08-19: 204 returned, crawl kept fetching pages
for minutes afterward). This migration adds the DB-backed flag the crawl
loop now polls cooperatively between bulk-fetch chunks
(``crawl4ai_client._chunked_bulk_fetch``), and a ``'cancelled'`` terminal
status distinct from ``'failed'`` — an operator-requested stop is not a
failure.

Two changes to ``knowledge.crawl_jobs``:

1. ADD COLUMN ``cancel_requested boolean NOT NULL DEFAULT false`` — set by
   the cancel endpoint, polled by ``adapters.crawler.run_crawl_job`` via a
   ``cancel_check`` closure passed down into ``crawl_site``.
2. EXTEND the status CHECK constraint to allow ``'cancelled'``, alongside
   the existing ``pending``/``running``/``completed``/``failed``/
   ``failed_partial`` values.

Idempotent: ADD COLUMN uses IF NOT EXISTS; the CHECK swap drops by name and
re-adds, both wrapped so re-running on a partially-migrated DB is safe —
same pattern as 0004_crawl_jobs_error_summary.py.

Revision ID: dafd7070493d
Revises: d0fb00b16473
Create Date: 2026-08-19
SPEC: crawl-cancel (ad hoc — cancel a running crawl actually stops it)
"""

from collections.abc import Sequence

from alembic import op

revision: str = "dafd7070493d"
down_revision: str | None = "d0fb00b16473"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge.crawl_jobs "
        "ADD COLUMN IF NOT EXISTS cancel_requested boolean NOT NULL DEFAULT false"
    )

    # Drop the old CHECK constraint and re-add with 'cancelled'. Wrapped in
    # DO blocks so re-running on a partially-migrated DB is safe.
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
            'failed_partial'::text,
            'cancelled'::text
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
            'failed'::text,
            'failed_partial'::text
        ]))
        """
    )
    op.execute("ALTER TABLE knowledge.crawl_jobs DROP COLUMN IF EXISTS cancel_requested")
