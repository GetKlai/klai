-- Migration 004: per-org enrichment config + Procrastinate schema
-- Run: psql -d klai -f 004_knowledge_org_config.sql

-- Per-org enrichment configuration (sparse override table)
CREATE TABLE IF NOT EXISTS knowledge.org_config (
    org_id              TEXT PRIMARY KEY,
    enrichment_enabled  BOOLEAN,        -- NULL = use global default (true)
    extra_config        JSONB NOT NULL DEFAULT '{}',
    updated_at          BIGINT NOT NULL
);

-- NOTIFY trigger for cache invalidation
CREATE OR REPLACE FUNCTION knowledge.notify_org_config_changed()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('org_config_changed', NEW.org_id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS org_config_changed_trigger ON knowledge.org_config;
CREATE TRIGGER org_config_changed_trigger
    AFTER INSERT OR UPDATE ON knowledge.org_config
    FOR EACH ROW EXECUTE FUNCTION knowledge.notify_org_config_changed();

-- Procrastinate task queue schema
-- Source: https://procrastinate.readthedocs.io/en/stable/sql.html
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS procrastinate_jobs (
    id                  BIGSERIAL PRIMARY KEY,
    queue_name          TEXT NOT NULL,
    task_name           TEXT NOT NULL,
    lock                TEXT,
    queueing_lock       TEXT,
    args                JSONB NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'todo'
                            CHECK (status IN ('todo', 'doing', 'succeeded', 'failed', 'cancelled', 'aborting', 'aborted')),
    scheduled_at        TIMESTAMPTZ,
    attempts            INTEGER NOT NULL DEFAULT 0,
    priority            INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at          TIMESTAMPTZ,
    ended_at            TIMESTAMPTZ,
    next_attempt_at     TIMESTAMPTZ,
    abort_requested     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS procrastinate_jobs_queue_name ON procrastinate_jobs (queue_name);
CREATE INDEX IF NOT EXISTS procrastinate_jobs_status ON procrastinate_jobs (status);
CREATE INDEX IF NOT EXISTS procrastinate_jobs_scheduled_at ON procrastinate_jobs (scheduled_at) WHERE status = 'todo';
CREATE UNIQUE INDEX IF NOT EXISTS procrastinate_jobs_queueing_lock_idx
    ON procrastinate_jobs (queueing_lock) WHERE status IN ('todo', 'doing');

CREATE TABLE IF NOT EXISTS procrastinate_periodic_defers (
    task_name           TEXT NOT NULL,
    defer_timestamp     TIMESTAMPTZ,
    id                  BIGINT REFERENCES procrastinate_jobs(id) ON DELETE SET NULL,
    PRIMARY KEY (task_name)
);

CREATE TABLE IF NOT EXISTS procrastinate_events (
    id                  BIGSERIAL PRIMARY KEY,
    job_id              BIGINT NOT NULL REFERENCES procrastinate_jobs(id) ON DELETE CASCADE,
    type                TEXT NOT NULL CHECK (type IN ('deferred', 'started', 'deferred_for_retry', 'failed', 'succeeded', 'cancelled', 'scheduled', 'aborted')),
    at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS procrastinate_events_job_id ON procrastinate_events (job_id);

CREATE OR REPLACE FUNCTION procrastinate_notify_queue()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'todo' THEN
        PERFORM pg_notify('procrastinate_queue#' || NEW.queue_name, NEW.id::text);
        PERFORM pg_notify('procrastinate_any_queue', NEW.id::text);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS procrastinate_notify_queue_trigger ON procrastinate_jobs;
CREATE TRIGGER procrastinate_notify_queue_trigger
    AFTER INSERT OR UPDATE OF status ON procrastinate_jobs
    FOR EACH ROW EXECUTE FUNCTION procrastinate_notify_queue();

CREATE OR REPLACE FUNCTION procrastinate_fetch_job(
    queues          TEXT[],
    nb_jobs         INTEGER,
    p_base_priority INTEGER DEFAULT 0
)
RETURNS SETOF procrastinate_jobs AS $$
DECLARE
    v_lock_timeout  CONSTANT INTERVAL := INTERVAL '100 ms';
    v_now           TIMESTAMPTZ := NOW();
    v_job           procrastinate_jobs;
    v_count         INTEGER := 0;
BEGIN
    FOR v_job IN
        SELECT * FROM procrastinate_jobs
        WHERE status = 'todo'
          AND (scheduled_at IS NULL OR scheduled_at <= v_now)
          AND (queues IS NULL OR queue_name = ANY(queues))
        ORDER BY priority DESC, id ASC
        FOR UPDATE SKIP LOCKED
        LIMIT nb_jobs
    LOOP
        UPDATE procrastinate_jobs
           SET status = 'doing',
               attempts = v_job.attempts + 1,
               started_at = v_now
         WHERE id = v_job.id;

        INSERT INTO procrastinate_events (job_id, type, at)
        VALUES (v_job.id, 'started', v_now);

        RETURN NEXT v_job;
        v_count := v_count + 1;
        IF v_count >= nb_jobs THEN
            RETURN;
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;
