-- NUQ (Node Universal Queue) schema for Firecrawl v1.x
-- Runs automatically on first start of firecrawl-postgres via /docker-entrypoint-initdb.d/
--
-- Derived from apps/nuq-postgres/nuq.sql in mendableai/firecrawl.
-- pg_cron and ALTER SYSTEM SET statements are omitted (not available in postgres:16-alpine).
-- Cron-based cleanup jobs are also omitted — handled by firecrawl workers via RabbitMQ.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS nuq;

DO $$ BEGIN
  CREATE TYPE nuq.job_status AS ENUM ('queued', 'active', 'completed', 'failed');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
  CREATE TYPE nuq.group_status AS ENUM ('active', 'completed', 'cancelled');
EXCEPTION
  WHEN duplicate_object THEN null;
END $$;

-- ── queue_scrape ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS nuq.queue_scrape (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  status nuq.job_status NOT NULL DEFAULT 'queued'::nuq.job_status,
  data jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  priority int NOT NULL DEFAULT 0,
  lock uuid,
  locked_at timestamp with time zone,
  stalls integer,
  finished_at timestamp with time zone,
  listen_channel_id text,
  returnvalue jsonb,
  failedreason text,
  owner_id uuid,
  group_id uuid,
  CONSTRAINT queue_scrape_pkey PRIMARY KEY (id)
);

ALTER TABLE nuq.queue_scrape
SET (autovacuum_vacuum_scale_factor = 0.01,
     autovacuum_analyze_scale_factor = 0.01,
     autovacuum_vacuum_cost_limit = 10000,
     autovacuum_vacuum_cost_delay = 0);

CREATE INDEX IF NOT EXISTS queue_scrape_active_locked_at_idx ON nuq.queue_scrape USING btree (locked_at) WHERE (status = 'active'::nuq.job_status);
CREATE INDEX IF NOT EXISTS nuq_queue_scrape_queued_optimal_2_idx ON nuq.queue_scrape (priority ASC, created_at ASC, id) WHERE (status = 'queued'::nuq.job_status);
CREATE INDEX IF NOT EXISTS nuq_queue_scrape_failed_created_at_idx ON nuq.queue_scrape USING btree (created_at) WHERE (status = 'failed'::nuq.job_status);
CREATE INDEX IF NOT EXISTS nuq_queue_scrape_completed_created_at_idx ON nuq.queue_scrape USING btree (created_at) WHERE (status = 'completed'::nuq.job_status);
CREATE INDEX IF NOT EXISTS nuq_queue_scrape_group_owner_mode_idx ON nuq.queue_scrape (group_id, owner_id) WHERE ((data->>'mode') = 'single_urls');
CREATE INDEX IF NOT EXISTS nuq_queue_scrape_group_mode_status_idx ON nuq.queue_scrape (group_id, status) WHERE ((data->>'mode') = 'single_urls');
CREATE INDEX IF NOT EXISTS nuq_queue_scrape_group_completed_listing_idx ON nuq.queue_scrape (group_id, finished_at ASC, created_at ASC) WHERE (status = 'completed'::nuq.job_status AND (data->>'mode') = 'single_urls');
CREATE INDEX IF NOT EXISTS idx_queue_scrape_group_status ON nuq.queue_scrape (group_id, status) WHERE status IN ('active', 'queued');

-- ── queue_scrape_backlog ──────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS nuq.queue_scrape_backlog (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  data jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  priority int NOT NULL DEFAULT 0,
  listen_channel_id text,
  owner_id uuid,
  group_id uuid,
  times_out_at timestamptz,
  CONSTRAINT queue_scrape_backlog_pkey PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS nuq_queue_scrape_backlog_owner_id_idx ON nuq.queue_scrape_backlog (owner_id);
CREATE INDEX IF NOT EXISTS nuq_queue_scrape_backlog_group_mode_idx ON nuq.queue_scrape_backlog (group_id) WHERE ((data->>'mode') = 'single_urls');
CREATE INDEX IF NOT EXISTS idx_queue_scrape_backlog_group_id ON nuq.queue_scrape_backlog (group_id);

-- ── queue_crawl_finished ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS nuq.queue_crawl_finished (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  status nuq.job_status NOT NULL DEFAULT 'queued'::nuq.job_status,
  data jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  priority int NOT NULL DEFAULT 0,
  lock uuid,
  locked_at timestamp with time zone,
  stalls integer,
  finished_at timestamp with time zone,
  listen_channel_id text,
  returnvalue jsonb,
  failedreason text,
  owner_id uuid,
  group_id uuid,
  CONSTRAINT queue_crawl_finished_pkey PRIMARY KEY (id)
);

ALTER TABLE nuq.queue_crawl_finished
SET (autovacuum_vacuum_scale_factor = 0.01,
     autovacuum_analyze_scale_factor = 0.01,
     autovacuum_vacuum_cost_limit = 10000,
     autovacuum_vacuum_cost_delay = 0);

CREATE INDEX IF NOT EXISTS queue_crawl_finished_active_locked_at_idx ON nuq.queue_crawl_finished USING btree (locked_at) WHERE (status = 'active'::nuq.job_status);
CREATE INDEX IF NOT EXISTS nuq_queue_crawl_finished_queued_optimal_2_idx ON nuq.queue_crawl_finished (priority ASC, created_at ASC, id) WHERE (status = 'queued'::nuq.job_status);
CREATE INDEX IF NOT EXISTS nuq_queue_crawl_finished_failed_created_at_idx ON nuq.queue_crawl_finished USING btree (created_at) WHERE (status = 'failed'::nuq.job_status);
CREATE INDEX IF NOT EXISTS nuq_queue_crawl_finished_completed_created_at_idx ON nuq.queue_crawl_finished USING btree (created_at) WHERE (status = 'completed'::nuq.job_status);

-- ── group_crawl ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS nuq.group_crawl (
  id uuid NOT NULL,
  status nuq.group_status NOT NULL DEFAULT 'active'::nuq.group_status,
  created_at timestamptz NOT NULL DEFAULT now(),
  owner_id uuid NOT NULL,
  ttl int8 NOT NULL DEFAULT 86400000,
  expires_at timestamptz,
  CONSTRAINT group_crawl_pkey PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_group_crawl_status ON nuq.group_crawl (status) WHERE status = 'active'::nuq.group_status;
