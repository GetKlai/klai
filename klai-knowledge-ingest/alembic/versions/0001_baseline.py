"""SPEC-INGEST-ALEMBIC-001 0001_baseline -- knowledge schema as of 2026-05-05.

Generated from `pg_dump --schema-only --schema=knowledge` on core-01 prod.
Captures every table, sequence, PK/FK constraint, index, trigger, and
PL/pgSQL function in the `knowledge` schema as it exists in production
on the date above.

Idempotent: every CREATE wraps in IF NOT EXISTS or a DO-block existence
check (pg_constraint / pg_trigger lookup). Safe to:
  - alembic stamp 0001_baseline on prod (records version, runs no DDL)
  - alembic upgrade head on a fresh dev stack (creates schema from empty)
  - alembic upgrade head on a partially-migrated DB (each guard skips)

downgrade() is `pass` -- baseline is the floor; rolling back below it is
undefined.

IMPORTANT -- deploy procedure (SPEC-INGEST-ALEMBIC-001):
  Stamp prod BEFORE the first container restart with the new entrypoint.sh.
  The alembic_version table in the knowledge schema does not yet exist on prod.

  Post-merge stamp command (run on core-01 BEFORE restarting the service):
    ssh core-01 "docker exec klai-core-knowledge-ingest-1 alembic stamp 0001_baseline"

  After stamping, alembic current reports: 0001_baseline (head)
  Subsequent restarts run alembic upgrade head as a no-op and start normally.

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
    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #
    op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")

    # ------------------------------------------------------------------ #
    # Functions
    # ------------------------------------------------------------------ #
    op.execute("""

    CREATE OR REPLACE FUNCTION knowledge.notify_kb_config_changed()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM pg_notify('kb_config_changed', NEW.org_id || ':' || NEW.kb_slug);
        RETURN NEW;
    END;
    $$
    """)

    op.execute("""

    CREATE OR REPLACE FUNCTION knowledge.notify_org_config_changed()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM pg_notify('org_config_changed', NEW.org_id);
        RETURN NEW;
    END;
    $$
    """)

    # ------------------------------------------------------------------ #
    # Sequences (created before tables so DEFAULT nextval() resolves)
    # ------------------------------------------------------------------ #
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS knowledge.crawled_pages_id_seq"
        " START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
    )
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS knowledge.page_links_id_seq"
        " START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
    )
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS knowledge.parent_chunks_id_seq"
        " START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
    )
    op.execute(
        "CREATE SEQUENCE IF NOT EXISTS knowledge.rag_eval_results_id_seq"
        " START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1"
    )

    # ------------------------------------------------------------------ #
    # Tables
    # ------------------------------------------------------------------ #

    # artifacts -- single-column PK on id (uuid), PK added via ALTER below
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.artifacts (
        id uuid NOT NULL,
        org_id text NOT NULL,
        user_id text,
        provenance_type text NOT NULL,
        assertion_mode text NOT NULL,
        synthesis_depth smallint DEFAULT 0 NOT NULL,
        confidence text,
        belief_time_start bigint NOT NULL,
        belief_time_end bigint DEFAULT '253402300800'::bigint NOT NULL,
        superseded_by uuid,
        created_at bigint NOT NULL,
        kb_slug text DEFAULT ''::text NOT NULL,
        path text DEFAULT ''::text NOT NULL,
        content_type text DEFAULT 'unknown'::text NOT NULL,
        extra jsonb DEFAULT '{}'::jsonb NOT NULL,
        content_hash text,
        CONSTRAINT artifacts_assertion_mode_check CHECK (
            assertion_mode = ANY (ARRAY[
                'factual'::text, 'belief'::text, 'hypothesis'::text,
                'procedural'::text, 'quoted'::text, 'unknown'::text
            ])
        ),
        CONSTRAINT artifacts_confidence_check CHECK (
            confidence = ANY (ARRAY['high'::text, 'medium'::text, 'low'::text])
        ),
        CONSTRAINT artifacts_provenance_type_check CHECK (
            provenance_type = ANY (ARRAY[
                'observed'::text, 'extracted'::text,
                'synthesized'::text, 'revised'::text
            ])
        ),
        CONSTRAINT artifacts_synthesis_depth_check CHECK (
            synthesis_depth >= 0 AND synthesis_depth <= 4
        )
    )
    """)

    # artifact_entities -- composite PK (artifact_id, entity_id)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.artifact_entities (
        artifact_id uuid NOT NULL,
        entity_id uuid NOT NULL,
        resolved boolean DEFAULT false NOT NULL
    )
    """)

    # artifact_images -- composite PK (artifact_id, s3_key)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.artifact_images (
        artifact_id uuid NOT NULL,
        s3_key text NOT NULL,
        content_hash text NOT NULL
    )
    """)

    # crawl_domains -- composite PK (domain, org_id)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.crawl_domains (
        domain text NOT NULL,
        org_id text NOT NULL,
        css_selector text NOT NULL,
        selector_source text NOT NULL,
        created_at timestamp with time zone DEFAULT now() NOT NULL,
        updated_at timestamp with time zone DEFAULT now() NOT NULL,
        CONSTRAINT crawl_domains_selector_source_check CHECK (
            selector_source = ANY (ARRAY['user'::text, 'ai'::text])
        )
    )
    """)

    # crawl_jobs -- single-column PK on id (text)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.crawl_jobs (
        id text NOT NULL,
        org_id text NOT NULL,
        kb_slug text NOT NULL,
        config jsonb NOT NULL,
        status text DEFAULT 'pending'::text NOT NULL,
        pages_total integer DEFAULT 0 NOT NULL,
        pages_done integer DEFAULT 0 NOT NULL,
        error text,
        created_at bigint NOT NULL,
        updated_at bigint NOT NULL,
        CONSTRAINT crawl_jobs_status_check CHECK (
            status = ANY (ARRAY[
                'pending'::text, 'running'::text,
                'completed'::text, 'failed'::text
            ])
        )
    )
    """)

    # crawled_pages -- single-column PK on id (bigint serial)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.crawled_pages (
        id bigint DEFAULT nextval('knowledge.crawled_pages_id_seq'::regclass) NOT NULL,
        org_id text NOT NULL,
        kb_slug text NOT NULL,
        url text NOT NULL,
        content_hash text NOT NULL,
        raw_markdown text NOT NULL,
        crawled_at bigint NOT NULL,
        raw_html_hash text
    )
    """)

    # derivations -- composite PK (child_id, parent_id)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.derivations (
        child_id uuid NOT NULL,
        parent_id uuid NOT NULL,
        span_json jsonb
    )
    """)

    # embedding_queue -- single-column PK on id (uuid)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.embedding_queue (
        id uuid NOT NULL,
        artifact_id uuid NOT NULL,
        operation text NOT NULL,
        created_at bigint NOT NULL,
        processed_at bigint,
        CONSTRAINT embedding_queue_operation_check CHECK (
            operation = ANY (ARRAY['upsert'::text, 'delete'::text])
        )
    )
    """)

    # entities -- single-column PK on id (uuid)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.entities (
        id uuid NOT NULL,
        org_id text NOT NULL,
        name text NOT NULL,
        type text NOT NULL,
        created_at bigint NOT NULL,
        CONSTRAINT entities_type_check CHECK (
            type = ANY (ARRAY[
                'product_area'::text, 'feature'::text,
                'concept'::text, 'person'::text
            ])
        )
    )
    """)

    # kb_config -- composite PK (org_id, kb_slug)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.kb_config (
        org_id text NOT NULL,
        kb_slug text NOT NULL,
        visibility text DEFAULT 'internal'::text NOT NULL,
        updated_at timestamp with time zone DEFAULT now() NOT NULL
    )
    """)

    # org_config -- single-column PK on org_id
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.org_config (
        org_id text NOT NULL,
        enrichment_enabled boolean,
        extra_config jsonb DEFAULT '{}'::jsonb NOT NULL,
        updated_at bigint NOT NULL
    )
    """)

    # page_links -- single-column PK on id (bigint serial)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.page_links (
        id bigint DEFAULT nextval('knowledge.page_links_id_seq'::regclass) NOT NULL,
        org_id text NOT NULL,
        kb_slug text NOT NULL,
        from_url text NOT NULL,
        to_url text NOT NULL,
        link_text text DEFAULT ''::text NOT NULL
    )
    """)

    # parent_chunks -- single-column PK on id (bigint serial)
    # SPEC-RAG-PARENT-CHILD-001: large parent chunks for retrieval context
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.parent_chunks (
        id bigint DEFAULT nextval('knowledge.parent_chunks_id_seq'::regclass) NOT NULL,
        artifact_id uuid NOT NULL,
        org_id text NOT NULL,
        text text NOT NULL,
        token_count integer NOT NULL,
        "position" integer NOT NULL,
        created_at timestamp with time zone DEFAULT now() NOT NULL
    )
    """)

    # rag_eval_results -- single-column PK on id (bigint serial)
    op.execute("""
    CREATE TABLE IF NOT EXISTS knowledge.rag_eval_results (
        id bigint DEFAULT nextval('knowledge.rag_eval_results_id_seq'::regclass) NOT NULL,
        run_at timestamp with time zone DEFAULT now() NOT NULL,
        suite text NOT NULL,
        variant text DEFAULT 'baseline'::text NOT NULL,
        query_id text NOT NULL,
        context_precision double precision,
        context_recall double precision,
        faithfulness double precision,
        answer_relevance double precision,
        retrieved_chunk_ids text[],
        retrieval_ms integer,
        total_tokens integer,
        meta jsonb
    )
    """)

    # ------------------------------------------------------------------ #
    # Sequence ownership (must follow table creation)
    # ------------------------------------------------------------------ #
    op.execute(
        "ALTER SEQUENCE IF EXISTS knowledge.crawled_pages_id_seq"
        " OWNED BY knowledge.crawled_pages.id"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS knowledge.page_links_id_seq OWNED BY knowledge.page_links.id"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS knowledge.parent_chunks_id_seq"
        " OWNED BY knowledge.parent_chunks.id"
    )
    op.execute(
        "ALTER SEQUENCE IF EXISTS knowledge.rag_eval_results_id_seq"
        " OWNED BY knowledge.rag_eval_results.id"
    )

    # ------------------------------------------------------------------ #
    # Primary key constraints (via ALTER TABLE -- idempotent DO-block)
    # ------------------------------------------------------------------ #
    op.execute("""
    DO $pk_wrap$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'artifacts_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.artifacts
                ADD CONSTRAINT artifacts_pkey PRIMARY KEY (id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'artifact_entities_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.artifact_entities
                ADD CONSTRAINT artifact_entities_pkey PRIMARY KEY (artifact_id, entity_id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'artifact_images_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.artifact_images
                ADD CONSTRAINT artifact_images_pkey PRIMARY KEY (artifact_id, s3_key);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'crawl_domains_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.crawl_domains
                ADD CONSTRAINT crawl_domains_pkey PRIMARY KEY (domain, org_id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'crawl_jobs_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.crawl_jobs
                ADD CONSTRAINT crawl_jobs_pkey PRIMARY KEY (id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'crawled_pages_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.crawled_pages
                ADD CONSTRAINT crawled_pages_pkey PRIMARY KEY (id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'derivations_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.derivations
                ADD CONSTRAINT derivations_pkey PRIMARY KEY (child_id, parent_id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'embedding_queue_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.embedding_queue
                ADD CONSTRAINT embedding_queue_pkey PRIMARY KEY (id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'entities_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.entities
                ADD CONSTRAINT entities_pkey PRIMARY KEY (id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'kb_config_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.kb_config
                ADD CONSTRAINT kb_config_pkey PRIMARY KEY (org_id, kb_slug);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'org_config_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.org_config
                ADD CONSTRAINT org_config_pkey PRIMARY KEY (org_id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'page_links_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.page_links
                ADD CONSTRAINT page_links_pkey PRIMARY KEY (id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'parent_chunks_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.parent_chunks
                ADD CONSTRAINT parent_chunks_pkey PRIMARY KEY (id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'rag_eval_results_pkey' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.rag_eval_results
                ADD CONSTRAINT rag_eval_results_pkey PRIMARY KEY (id);
        END IF;

    END
    $pk_wrap$
    """)

    # ------------------------------------------------------------------ #
    # UNIQUE constraints
    # ------------------------------------------------------------------ #
    op.execute("""
    DO $uniq_wrap$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'crawled_pages_uniq' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.crawled_pages
                ADD CONSTRAINT crawled_pages_uniq UNIQUE (org_id, kb_slug, url);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'page_links_uniq' AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.page_links
                ADD CONSTRAINT page_links_uniq UNIQUE (org_id, kb_slug, from_url, to_url);
        END IF;
    END
    $uniq_wrap$
    """)

    # ------------------------------------------------------------------ #
    # Foreign key constraints
    # ------------------------------------------------------------------ #
    op.execute("""
    DO $fk_wrap$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'artifact_entities_artifact_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.artifact_entities
                ADD CONSTRAINT artifact_entities_artifact_id_fkey
                FOREIGN KEY (artifact_id)
                REFERENCES knowledge.artifacts(id) ON DELETE CASCADE;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'artifact_entities_entity_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.artifact_entities
                ADD CONSTRAINT artifact_entities_entity_id_fkey
                FOREIGN KEY (entity_id)
                REFERENCES knowledge.entities(id) ON DELETE CASCADE;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'artifact_images_artifact_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.artifact_images
                ADD CONSTRAINT artifact_images_artifact_id_fkey
                FOREIGN KEY (artifact_id)
                REFERENCES knowledge.artifacts(id) ON DELETE CASCADE;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'artifacts_superseded_by_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.artifacts
                ADD CONSTRAINT artifacts_superseded_by_fkey
                FOREIGN KEY (superseded_by)
                REFERENCES knowledge.artifacts(id);
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'derivations_child_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.derivations
                ADD CONSTRAINT derivations_child_id_fkey
                FOREIGN KEY (child_id)
                REFERENCES knowledge.artifacts(id) ON DELETE CASCADE;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'derivations_parent_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.derivations
                ADD CONSTRAINT derivations_parent_id_fkey
                FOREIGN KEY (parent_id)
                REFERENCES knowledge.artifacts(id) ON DELETE CASCADE;
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'parent_chunks_artifact_id_fkey'
              AND table_schema = 'knowledge'
        ) THEN
            ALTER TABLE ONLY knowledge.parent_chunks
                ADD CONSTRAINT parent_chunks_artifact_id_fkey
                FOREIGN KEY (artifact_id)
                REFERENCES knowledge.artifacts(id) ON DELETE CASCADE;
        END IF;

    END
    $fk_wrap$
    """)

    # ------------------------------------------------------------------ #
    # Indexes
    # ------------------------------------------------------------------ #
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifacts_active"
        " ON knowledge.artifacts USING btree (belief_time_end)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifacts_active_path"
        " ON knowledge.artifacts USING btree (org_id, kb_slug, path, belief_time_end)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifacts_org_id"
        " ON knowledge.artifacts USING btree (org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifacts_org_kb_path"
        " ON knowledge.artifacts USING btree (org_id, kb_slug, path)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_artifacts_user_id"
        " ON knowledge.artifacts USING btree (user_id)"
        " WHERE (user_id IS NOT NULL)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_crawled_pages_lookup"
        " ON knowledge.crawled_pages USING btree (org_id, kb_slug, url)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_embedding_queue_unprocessed"
        " ON knowledge.embedding_queue USING btree (created_at)"
        " WHERE (processed_at IS NULL)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_page_links_incoming"
        " ON knowledge.page_links USING btree (org_id, kb_slug, to_url)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_page_links_outgoing"
        " ON knowledge.page_links USING btree (org_id, kb_slug, from_url)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_artifact_images_content_hash"
        " ON knowledge.artifact_images USING btree (content_hash)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_parent_chunks_artifact"
        " ON knowledge.parent_chunks USING btree (artifact_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_parent_chunks_org"
        " ON knowledge.parent_chunks USING btree (org_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_eval_run_at_suite"
        " ON knowledge.rag_eval_results USING btree (run_at DESC, suite)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_eval_variant_run_at"
        " ON knowledge.rag_eval_results USING btree (variant, run_at DESC)"
    )

    # ------------------------------------------------------------------ #
    # Triggers
    # ------------------------------------------------------------------ #
    op.execute("""
    DO $tr_wrap$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'kb_config_changed_trigger'
        ) THEN
            CREATE TRIGGER kb_config_changed_trigger
                AFTER INSERT OR UPDATE ON knowledge.kb_config
                FOR EACH ROW EXECUTE FUNCTION knowledge.notify_kb_config_changed();
        END IF;

        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'org_config_changed_trigger'
        ) THEN
            CREATE TRIGGER org_config_changed_trigger
                AFTER INSERT OR UPDATE ON knowledge.org_config
                FOR EACH ROW EXECUTE FUNCTION knowledge.notify_org_config_changed();
        END IF;
    END
    $tr_wrap$
    """)


def downgrade() -> None:
    """Baseline migration -- no downgrade path."""
    pass
