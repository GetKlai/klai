"""SPEC-INGEST-RECONCILE-001 — add knowledge.crawl_jobs.fetch_outcomes.

Adds a JSONB column to capture per-URL fetch outcomes from crawl4ai's
``POST /crawl`` bulk endpoint. Each entry has shape::

    {"url": str, "reason_code": str, "status_code": int|null, "content_length": int}

``reason_code`` MUST be a member of
:class:`knowledge_ingest.reason_codes.FetchReasonCode`. The CHECK
constraint enforces that ``fetch_outcomes`` is a JSONB array (or NULL);
per-element ``reason_code`` validation is done application-side because
inline jsonb-element CHECKs require subqueries that Postgres rejects in
a column-level constraint.

Idempotent: ADD COLUMN uses IF NOT EXISTS; constraint is wrapped in a DO
block that DROPs by name before re-adding, so re-running on a partially
migrated DB is safe.

Revision ID: a8c5e1d2f3b4
Revises: 603787256fb8
Create Date: 2026-05-06
SPEC: SPEC-INGEST-RECONCILE-001 AC-4, AC-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a8c5e1d2f3b4"
down_revision: str | None = "603787256fb8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge.crawl_jobs "
        "ADD COLUMN IF NOT EXISTS fetch_outcomes jsonb NOT NULL DEFAULT '[]'::jsonb"
    )

    # Shape guard only — element-level reason_code validation is application-side
    # (the StrEnum FetchReasonCode + classifier in crawl4ai_client._classify_fetch_outcome).
    # Postgres rejects subquery-based CHECKs inline; a function-based CHECK would
    # add a migration-management burden disproportionate to the gain at Voys-scale.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'crawl_jobs_fetch_outcomes_is_array'
                  AND conrelid = 'knowledge.crawl_jobs'::regclass
            ) THEN
                ALTER TABLE knowledge.crawl_jobs
                DROP CONSTRAINT crawl_jobs_fetch_outcomes_is_array;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_jobs
        ADD CONSTRAINT crawl_jobs_fetch_outcomes_is_array
        CHECK (jsonb_typeof(fetch_outcomes) = 'array')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'crawl_jobs_fetch_outcomes_is_array'
                  AND conrelid = 'knowledge.crawl_jobs'::regclass
            ) THEN
                ALTER TABLE knowledge.crawl_jobs
                DROP CONSTRAINT crawl_jobs_fetch_outcomes_is_array;
            END IF;
        END
        $$;
        """
    )
    op.execute("ALTER TABLE knowledge.crawl_jobs DROP COLUMN IF EXISTS fetch_outcomes")
