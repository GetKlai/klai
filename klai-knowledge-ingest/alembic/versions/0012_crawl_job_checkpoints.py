"""SPEC-CRAWL-DURABILITY-001 — durable crawl frontier checkpoints.

Revision ID: c4a11d9b7e20
Revises: dafd7070493d
Create Date: 2026-08-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4a11d9b7e20"
down_revision: str | None = "dafd7070493d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE knowledge.crawl_jobs
          ADD COLUMN IF NOT EXISTS execution_generation bigint NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS checkpoint_sequence bigint NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS checkpoint_updated_at timestamptz,
          ADD COLUMN IF NOT EXISTS runtime_checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
          ADD COLUMN IF NOT EXISTS recovery_count integer NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS rate_limit_effect_applied boolean NOT NULL DEFAULT false
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_jobs
          ADD CONSTRAINT crawl_jobs_runtime_checkpoint_is_object
          CHECK (jsonb_typeof(runtime_checkpoint) = 'object')
        """
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_jobs
          ADD CONSTRAINT crawl_jobs_id_org_id_key UNIQUE (id, org_id)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge.crawl_job_frontier (
          job_id text NOT NULL,
          org_id text NOT NULL,
          crawl_scope text NOT NULL CHECK (crawl_scope IN ('primary', 'discovery_seed')),
          canonical_url text NOT NULL,
          url text NOT NULL,
          depth integer NOT NULL CHECK (depth >= 0),
          discovered_from text,
          source_kind text NOT NULL CHECK (source_kind IN ('start', 'sitemap', 'page_link')),
          priority integer NOT NULL,
          discovery_order integer NOT NULL CHECK (discovery_order > 0),
          state text NOT NULL CHECK (state IN ('queued', 'fetched', 'omitted')),
          reason_code text,
          result jsonb CHECK (result IS NULL OR jsonb_typeof(result) = 'object'),
          outcome jsonb CHECK (outcome IS NULL OR jsonb_typeof(outcome) = 'object'),
          PRIMARY KEY (job_id, crawl_scope, canonical_url),
          CONSTRAINT crawl_job_frontier_job_tenant_fk
            FOREIGN KEY (job_id, org_id)
            REFERENCES knowledge.crawl_jobs(id, org_id) ON DELETE CASCADE
        )
        """
    )
    op.execute("ALTER TABLE knowledge.crawl_job_frontier ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge.crawl_job_frontier FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $migration$
        BEGIN
          -- Existing installations get the stricter SECURITY DEFINER helper
          -- from post-deploy SQL. A fresh database still needs a working,
          -- default-deny policy before that optional hardening step runs.
          IF to_regprocedure('knowledge._rls_current_org_id()') IS NULL THEN
            EXECUTE $function$
              CREATE FUNCTION knowledge._rls_current_org_id()
                RETURNS text
                LANGUAGE sql
                STABLE
                AS $body$
                  SELECT NULLIF(current_setting('app.current_org_id', true), '')
                $body$
            $function$;
          END IF;
        END;
        $migration$
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON knowledge.crawl_job_frontier
          AS RESTRICTIVE
          USING (org_id = knowledge._rls_current_org_id())
          WITH CHECK (org_id = knowledge._rls_current_org_id())
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge.crawl_job_frontier")
    op.execute(
        "ALTER TABLE knowledge.crawl_jobs DROP CONSTRAINT IF EXISTS crawl_jobs_id_org_id_key"
    )
    op.execute(
        "ALTER TABLE knowledge.crawl_jobs "
        "DROP CONSTRAINT IF EXISTS crawl_jobs_runtime_checkpoint_is_object"
    )
    op.execute(
        """
        ALTER TABLE knowledge.crawl_jobs
          DROP COLUMN IF EXISTS rate_limit_effect_applied,
          DROP COLUMN IF EXISTS recovery_count,
          DROP COLUMN IF EXISTS runtime_checkpoint,
          DROP COLUMN IF EXISTS checkpoint_updated_at,
          DROP COLUMN IF EXISTS checkpoint_sequence,
          DROP COLUMN IF EXISTS execution_generation
        """
    )
