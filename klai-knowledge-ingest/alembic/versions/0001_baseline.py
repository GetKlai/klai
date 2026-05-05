"""Baseline migration: knowledge schema as of 2026-05-05.

This migration reflects the production schema extracted via pg_dump on 2026-05-05.

IMPORTANT -- deploy procedure (SPEC-INGEST-ALEMBIC-001):
  Stamp prod BEFORE the first container restart with the new entrypoint.sh.
  The alembic_version table in the knowledge schema does not yet exist on prod.

  Post-merge stamp command (run on core-01 BEFORE restarting the service):
    ssh core-01 "docker exec klai-core-knowledge-ingest-1 alembic stamp 0001_baseline"

  After stamping, alembic current reports: 0001_baseline (head)
  Subsequent restarts run alembic upgrade head as a no-op and start normally.

Also inlines the content of the former migrations/001_crawl_domains.sql
(SPEC-CRAWL-001 R-2, R-3) which is removed from the repo by this PR.

Revision ID: 0001_baseline
Revises: None
Create Date: 2026-05-05
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Bootstrap knowledge schema -- all DDL is idempotent (IF NOT EXISTS).

    Safe to stamp on production (skip execution) OR to run against an empty
    database (fresh dev stack).
    """
    op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")

    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS knowledge.crawled_pages_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
    )
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS knowledge.page_links_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
    )
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS knowledge.rag_eval_results_id_seq AS integer START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
    )

    op.execute("""
    CREATE OR REPLACE FUNCTION knowledge.notify_kb_change()
    RETURNS trigger LANGUAGE plpgsql AS $notify_kb$
    BEGIN
        PERFORM pg_notify(
            'kb_change',
            json_build_object('table', TG_TABLE_NAME, 'org_id', NEW.org_id)::text
        );
        RETURN NEW;
    END;
    $notify_kb$
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.artifacts (
        id TEXT NOT NULL,
        org_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        artifact_type TEXT NOT NULL,
        uri TEXT,
        title TEXT,
        content TEXT,
        content_fingerprint TEXT,
        content_length INTEGER,
        content_chunks INTEGER,
        metadata JSONB DEFAULT '{}' NOT NULL,
        embedding_status TEXT DEFAULT 'pending' NOT NULL,
        embedding_model TEXT,
        embedding_dims INTEGER,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        provider TEXT,
        provider_item_id TEXT,
        provider_modified_at TIMESTAMP WITH TIME ZONE,
        sync_error TEXT,
        language TEXT,
        CONSTRAINT artifacts_pkey PRIMARY KEY (id, org_id),
        CONSTRAINT artifacts_artifact_type_check CHECK (
            artifact_type IN ('page', 'document', 'note', 'image', 'video', 'audio', 'email', 'event', 'contact', 'task', 'message', 'file', 'spreadsheet', 'presentation', 'code', 'other')
        ),
        CONSTRAINT artifacts_embedding_status_check CHECK (
            embedding_status IN ('pending', 'processing', 'done', 'error', 'skipped')
        )
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.artifact_entities (
        artifact_id TEXT NOT NULL,
        org_id TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        CONSTRAINT artifact_entities_pkey PRIMARY KEY (artifact_id, org_id, entity_id)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.artifact_images (
        id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        org_id TEXT NOT NULL,
        s3_key TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        width INTEGER,
        height INTEGER,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT artifact_images_pkey PRIMARY KEY (id, org_id)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.crawl_domains (
        domain TEXT NOT NULL,
        org_id TEXT NOT NULL,
        css_selector TEXT NOT NULL,
        selector_source TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT crawl_domains_pkey PRIMARY KEY (domain, org_id),
        CONSTRAINT crawl_domains_selector_source_check CHECK (
            selector_source IN ('user', 'ai')
        )
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.crawl_jobs (
        id TEXT NOT NULL,
        org_id TEXT NOT NULL,
        domain TEXT NOT NULL,
        status TEXT DEFAULT 'pending' NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE,
        pages_crawled INTEGER DEFAULT 0 NOT NULL,
        pages_failed INTEGER DEFAULT 0 NOT NULL,
        error TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT crawl_jobs_pkey PRIMARY KEY (id, org_id),
        CONSTRAINT crawl_jobs_status_check CHECK (
            status IN ('pending', 'running', 'done', 'error', 'cancelled')
        )
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.crawled_pages (
        id INTEGER DEFAULT nextval('knowledge.crawled_pages_id_seq') NOT NULL,
        org_id TEXT NOT NULL,
        job_id TEXT NOT NULL,
        url TEXT NOT NULL,
        status TEXT DEFAULT 'pending' NOT NULL,
        artifact_id TEXT,
        error TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT crawled_pages_pkey PRIMARY KEY (id, org_id),
        CONSTRAINT crawled_pages_status_check CHECK (
            status IN ('pending', 'processing', 'done', 'error', 'skipped')
        )
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.derivations (
        id TEXT NOT NULL,
        org_id TEXT NOT NULL,
        source_artifact_id TEXT NOT NULL,
        derived_artifact_id TEXT NOT NULL,
        derivation_type TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT derivations_pkey PRIMARY KEY (id, org_id)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.embedding_queue (
        id TEXT NOT NULL,
        org_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        priority INTEGER DEFAULT 0 NOT NULL,
        status TEXT DEFAULT 'pending' NOT NULL,
        attempts INTEGER DEFAULT 0 NOT NULL,
        last_error TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT embedding_queue_pkey PRIMARY KEY (id, org_id),
        CONSTRAINT embedding_queue_status_check CHECK (
            status IN ('pending', 'processing', 'done', 'error')
        )
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.entities (
        id TEXT NOT NULL,
        org_id TEXT NOT NULL,
        name TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        metadata JSONB DEFAULT '{}' NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT entities_pkey PRIMARY KEY (id, org_id)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.kb_config (
        org_id TEXT NOT NULL,
        embedding_model TEXT DEFAULT 'text-embedding-3-small' NOT NULL,
        embedding_dims INTEGER DEFAULT 1536 NOT NULL,
        reranker_model TEXT,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT kb_config_pkey PRIMARY KEY (org_id)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.org_config (
        org_id TEXT NOT NULL,
        config JSONB DEFAULT '{}' NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT org_config_pkey PRIMARY KEY (org_id)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.page_links (
        id INTEGER DEFAULT nextval('knowledge.page_links_id_seq') NOT NULL,
        org_id TEXT NOT NULL,
        from_page_id INTEGER NOT NULL,
        to_url TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT page_links_pkey PRIMARY KEY (id, org_id)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.rag_eval_results (
        id INTEGER DEFAULT nextval('knowledge.rag_eval_results_id_seq') NOT NULL,
        org_id TEXT NOT NULL,
        query TEXT NOT NULL,
        retrieved_artifact_ids TEXT[] NOT NULL,
        relevant_artifact_ids TEXT[] NOT NULL,
        precision_at_k DOUBLE PRECISION,
        recall_at_k DOUBLE PRECISION,
        ndcg_at_k DOUBLE PRECISION,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        CONSTRAINT rag_eval_results_pkey PRIMARY KEY (id, org_id)
    )
    """)

    op.execute(
        "ALTER SEQUENCE IF EXISTS knowledge.crawled_pages_id_seq OWNED BY knowledge.crawled_pages.id"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS knowledge.page_links_id_seq OWNED BY knowledge.page_links.id"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS knowledge.rag_eval_results_id_seq OWNED BY knowledge.rag_eval_results.id"
    )

    op.execute("""
    DO $fk_wrap$
    BEGIN
        -- artifact_entities -> artifacts
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'artifact_entities_artifact_id_org_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE knowledge.artifact_entities
                ADD CONSTRAINT artifact_entities_artifact_id_org_id_fkey
                FOREIGN KEY (artifact_id, org_id)
                REFERENCES knowledge.artifacts(id, org_id) ON DELETE CASCADE;
        END IF;
        -- artifact_images -> artifacts
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'artifact_images_artifact_id_org_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE knowledge.artifact_images
                ADD CONSTRAINT artifact_images_artifact_id_org_id_fkey
                FOREIGN KEY (artifact_id, org_id)
                REFERENCES knowledge.artifacts(id, org_id) ON DELETE CASCADE;
        END IF;
        -- crawled_pages -> crawl_jobs
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'crawled_pages_job_id_org_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE knowledge.crawled_pages
                ADD CONSTRAINT crawled_pages_job_id_org_id_fkey
                FOREIGN KEY (job_id, org_id)
                REFERENCES knowledge.crawl_jobs(id, org_id) ON DELETE CASCADE;
        END IF;
        -- derivations -> artifacts (source)
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'derivations_source_artifact_id_org_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE knowledge.derivations
                ADD CONSTRAINT derivations_source_artifact_id_org_id_fkey
                FOREIGN KEY (source_artifact_id, org_id)
                REFERENCES knowledge.artifacts(id, org_id) ON DELETE CASCADE;
        END IF;
        -- derivations -> artifacts (derived)
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'derivations_derived_artifact_id_org_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE knowledge.derivations
                ADD CONSTRAINT derivations_derived_artifact_id_org_id_fkey
                FOREIGN KEY (derived_artifact_id, org_id)
                REFERENCES knowledge.artifacts(id, org_id) ON DELETE CASCADE;
        END IF;
        -- embedding_queue -> artifacts
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'embedding_queue_artifact_id_org_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE knowledge.embedding_queue
                ADD CONSTRAINT embedding_queue_artifact_id_org_id_fkey
                FOREIGN KEY (artifact_id, org_id)
                REFERENCES knowledge.artifacts(id, org_id) ON DELETE CASCADE;
        END IF;
    END
    $fk_wrap$
    """)

    op.execute("CREATE INDEX IF NOT EXISTS artifacts_org_id_idx ON knowledge.artifacts (org_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS artifacts_source_id_idx ON knowledge.artifacts (source_id, org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS artifacts_embedding_status_idx ON knowledge.artifacts (embedding_status, org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS artifacts_provider_item_idx ON knowledge.artifacts (provider, provider_item_id, org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS artifact_entities_entity_id_idx ON knowledge.artifact_entities (entity_id, org_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS crawl_jobs_org_id_idx ON knowledge.crawl_jobs (org_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS crawl_jobs_status_idx ON knowledge.crawl_jobs (status, org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS crawled_pages_job_id_idx ON knowledge.crawled_pages (job_id, org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS embedding_queue_status_priority_idx ON knowledge.embedding_queue (status, priority DESC, created_at) WHERE status = 'pending'"
    )
    op.execute("CREATE INDEX IF NOT EXISTS entities_org_id_idx ON knowledge.entities (org_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS derivations_source_idx ON knowledge.derivations (source_artifact_id, org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS derivations_derived_idx ON knowledge.derivations (derived_artifact_id, org_id)"
    )

    op.execute("""
    DO $tr_wrap$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'kb_config_changed_trigger'
        ) THEN
            CREATE TRIGGER kb_config_changed_trigger
                AFTER INSERT OR UPDATE ON knowledge.kb_config
                FOR EACH ROW EXECUTE FUNCTION knowledge.notify_kb_change();
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'org_config_changed_trigger'
        ) THEN
            CREATE TRIGGER org_config_changed_trigger
                AFTER INSERT OR UPDATE ON knowledge.org_config
                FOR EACH ROW EXECUTE FUNCTION knowledge.notify_kb_change();
        END IF;
    END
    $tr_wrap$
    """)


def downgrade() -> None:
    """Baseline migration -- no downgrade path."""
    pass
