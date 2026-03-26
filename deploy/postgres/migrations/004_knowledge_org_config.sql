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
-- Official procrastinate 2.x schema (procrastinate>=2.0.0)
-- Source: procrastinate/sql/schema.sql from the installed package

CREATE EXTENSION IF NOT EXISTS plpgsql WITH SCHEMA pg_catalog;

CREATE TYPE procrastinate_job_status AS ENUM (
    'todo',
    'doing',
    'succeeded',
    'failed',
    'cancelled',
    'aborting',
    'aborted'
);

CREATE TYPE procrastinate_job_event_type AS ENUM (
    'deferred',
    'started',
    'deferred_for_retry',
    'failed',
    'succeeded',
    'cancelled',
    'abort_requested',
    'aborted',
    'scheduled'
);

CREATE TABLE IF NOT EXISTS procrastinate_jobs (
    id bigserial PRIMARY KEY,
    queue_name character varying(128) NOT NULL,
    task_name character varying(128) NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    lock text,
    queueing_lock text,
    args jsonb DEFAULT '{}' NOT NULL,
    status procrastinate_job_status DEFAULT 'todo'::procrastinate_job_status NOT NULL,
    scheduled_at timestamp with time zone NULL,
    attempts integer DEFAULT 0 NOT NULL
);

CREATE TABLE IF NOT EXISTS procrastinate_periodic_defers (
    id bigserial PRIMARY KEY,
    task_name character varying(128) NOT NULL,
    defer_timestamp bigint,
    job_id bigint REFERENCES procrastinate_jobs(id) NULL,
    periodic_id character varying(128) NOT NULL DEFAULT '',
    CONSTRAINT procrastinate_periodic_defers_unique UNIQUE (task_name, periodic_id, defer_timestamp)
);

CREATE TABLE IF NOT EXISTS procrastinate_events (
    id bigserial PRIMARY KEY,
    job_id bigint NOT NULL REFERENCES procrastinate_jobs ON DELETE CASCADE,
    type procrastinate_job_event_type,
    at timestamp with time zone DEFAULT NOW() NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS procrastinate_jobs_queueing_lock_idx ON procrastinate_jobs (queueing_lock) WHERE status = 'todo';
CREATE UNIQUE INDEX IF NOT EXISTS procrastinate_jobs_lock_idx ON procrastinate_jobs (lock) WHERE status = 'doing';
CREATE INDEX IF NOT EXISTS procrastinate_jobs_queue_name_idx ON procrastinate_jobs(queue_name);
CREATE INDEX IF NOT EXISTS procrastinate_jobs_id_lock_idx ON procrastinate_jobs (id, lock) WHERE status = ANY (ARRAY['todo'::procrastinate_job_status, 'doing'::procrastinate_job_status, 'aborting'::procrastinate_job_status]);
CREATE INDEX IF NOT EXISTS procrastinate_jobs_priority_idx ON procrastinate_jobs(priority desc, id asc) WHERE (status = 'todo'::procrastinate_job_status);
CREATE INDEX IF NOT EXISTS procrastinate_events_job_id_fkey ON procrastinate_events(job_id);
CREATE INDEX IF NOT EXISTS procrastinate_periodic_defers_job_id_fkey ON procrastinate_periodic_defers(job_id);

CREATE OR REPLACE FUNCTION procrastinate_defer_job(
    queue_name character varying,
    task_name character varying,
    priority integer,
    lock text,
    queueing_lock text,
    args jsonb,
    scheduled_at timestamp with time zone
)
    RETURNS bigint
    LANGUAGE plpgsql
AS $$
DECLARE
    job_id bigint;
BEGIN
    INSERT INTO procrastinate_jobs (queue_name, task_name, priority, lock, queueing_lock, args, scheduled_at)
    VALUES (queue_name, task_name, priority, lock, queueing_lock, args, scheduled_at)
    RETURNING id INTO job_id;

    RETURN job_id;
END;
$$;

CREATE OR REPLACE FUNCTION procrastinate_fetch_job(
    target_queue_names character varying[]
)
    RETURNS procrastinate_jobs
    LANGUAGE plpgsql
AS $$
DECLARE
    found_jobs procrastinate_jobs;
BEGIN
    WITH candidate AS (
        SELECT jobs.*
            FROM procrastinate_jobs AS jobs
            WHERE
                NOT EXISTS (
                    SELECT 1
                        FROM procrastinate_jobs AS earlier_jobs
                        WHERE
                            jobs.lock IS NOT NULL
                            AND earlier_jobs.lock = jobs.lock
                            AND earlier_jobs.status IN ('todo', 'doing', 'aborting')
                            AND earlier_jobs.id < jobs.id)
                AND jobs.status = 'todo'
                AND (target_queue_names IS NULL OR jobs.queue_name = ANY( target_queue_names ))
                AND (jobs.scheduled_at IS NULL OR jobs.scheduled_at <= now())
            ORDER BY jobs.priority DESC, jobs.id ASC LIMIT 1
            FOR UPDATE OF jobs SKIP LOCKED
    )
    UPDATE procrastinate_jobs
        SET status = 'doing'
        FROM candidate
        WHERE procrastinate_jobs.id = candidate.id
        RETURNING procrastinate_jobs.* INTO found_jobs;

    RETURN found_jobs;
END;
$$;

CREATE OR REPLACE FUNCTION procrastinate_finish_job(job_id bigint, end_status procrastinate_job_status, delete_job boolean)
    RETURNS void
    LANGUAGE plpgsql
AS $$
DECLARE
    _job_id bigint;
BEGIN
    IF end_status NOT IN ('succeeded', 'failed', 'aborted') THEN
        RAISE 'End status should be either "succeeded", "failed" or "aborted" (job id: %)', job_id;
    END IF;
    IF delete_job THEN
        DELETE FROM procrastinate_jobs
        WHERE id = job_id AND status IN ('todo', 'doing', 'aborting')
        RETURNING id INTO _job_id;
    ELSE
        UPDATE procrastinate_jobs
        SET status = end_status,
            attempts =
                CASE
                    WHEN status = 'doing' THEN attempts + 1
                    ELSE attempts
                END
        WHERE id = job_id AND status IN ('todo', 'doing', 'aborting')
        RETURNING id INTO _job_id;
    END IF;
    IF _job_id IS NULL THEN
        RAISE 'Job was not found or not in "doing", "todo" or "aborting" status (job id: %)', job_id;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION procrastinate_cancel_job(job_id bigint, abort boolean, delete_job boolean)
    RETURNS bigint
    LANGUAGE plpgsql
AS $$
DECLARE
    _job_id bigint;
BEGIN
    IF delete_job THEN
        DELETE FROM procrastinate_jobs
        WHERE id = job_id AND status = 'todo'
        RETURNING id INTO _job_id;
    END IF;
    IF _job_id IS NULL THEN
        IF abort THEN
            UPDATE procrastinate_jobs
            SET status = CASE status
                WHEN 'todo' THEN 'cancelled'::procrastinate_job_status
                WHEN 'doing' THEN 'aborting'::procrastinate_job_status
            END
            WHERE id = job_id AND status IN ('todo', 'doing')
            RETURNING id INTO _job_id;
        ELSE
            UPDATE procrastinate_jobs
            SET status = 'cancelled'::procrastinate_job_status
            WHERE id = job_id AND status = 'todo'
            RETURNING id INTO _job_id;
        END IF;
    END IF;
    RETURN _job_id;
END;
$$;

CREATE OR REPLACE FUNCTION procrastinate_retry_job(
    job_id bigint,
    retry_at timestamp with time zone,
    new_priority integer,
    new_queue_name character varying,
    new_lock character varying
)
    RETURNS void
    LANGUAGE plpgsql
AS $$
DECLARE
    _job_id bigint;
BEGIN
    UPDATE procrastinate_jobs
    SET status = 'todo',
        attempts = attempts + 1,
        scheduled_at = retry_at,
        priority = COALESCE(new_priority, priority),
        queue_name = COALESCE(new_queue_name, queue_name),
        lock = COALESCE(new_lock, lock)
    WHERE id = job_id AND status = 'doing'
    RETURNING id INTO _job_id;
    IF _job_id IS NULL THEN
        RAISE 'Job was not found or not in "doing" status (job id: %)', job_id;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION procrastinate_notify_queue()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_notify('procrastinate_queue#' || NEW.queue_name, NEW.task_name);
    PERFORM pg_notify('procrastinate_any_queue', NEW.task_name);
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION procrastinate_trigger_status_events_procedure_insert()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type)
        VALUES (NEW.id, 'deferred'::procrastinate_job_event_type);
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION procrastinate_trigger_status_events_procedure_update()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    WITH t AS (
        SELECT CASE
            WHEN OLD.status = 'todo'::procrastinate_job_status
                AND NEW.status = 'doing'::procrastinate_job_status
                THEN 'started'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'todo'::procrastinate_job_status
                THEN 'deferred_for_retry'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'failed'::procrastinate_job_status
                THEN 'failed'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'succeeded'::procrastinate_job_status
                THEN 'succeeded'::procrastinate_job_event_type
            WHEN OLD.status = 'todo'::procrastinate_job_status
                AND (
                    NEW.status = 'cancelled'::procrastinate_job_status
                    OR NEW.status = 'failed'::procrastinate_job_status
                    OR NEW.status = 'succeeded'::procrastinate_job_status
                )
                THEN 'cancelled'::procrastinate_job_event_type
            WHEN OLD.status = 'doing'::procrastinate_job_status
                AND NEW.status = 'aborting'::procrastinate_job_status
                THEN 'abort_requested'::procrastinate_job_event_type
            WHEN (
                    OLD.status = 'doing'::procrastinate_job_status
                    OR OLD.status = 'aborting'::procrastinate_job_status
                )
                AND NEW.status = 'aborted'::procrastinate_job_status
                THEN 'aborted'::procrastinate_job_event_type
            ELSE NULL
        END as event_type
    )
    INSERT INTO procrastinate_events(job_id, type)
        SELECT NEW.id, t.event_type
        FROM t
        WHERE t.event_type IS NOT NULL;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION procrastinate_trigger_scheduled_events_procedure()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO procrastinate_events(job_id, type, at)
        VALUES (NEW.id, 'scheduled'::procrastinate_job_event_type, NEW.scheduled_at);

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION procrastinate_unlink_periodic_defers()
    RETURNS trigger
    LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE procrastinate_periodic_defers
    SET job_id = NULL
    WHERE job_id = OLD.id;
    RETURN OLD;
END;
$$;

DROP TRIGGER IF EXISTS procrastinate_jobs_notify_queue ON procrastinate_jobs;
CREATE TRIGGER procrastinate_jobs_notify_queue
    AFTER INSERT ON procrastinate_jobs
    FOR EACH ROW WHEN ((new.status = 'todo'::procrastinate_job_status))
    EXECUTE PROCEDURE procrastinate_notify_queue();

DROP TRIGGER IF EXISTS procrastinate_trigger_status_events_update ON procrastinate_jobs;
CREATE TRIGGER procrastinate_trigger_status_events_update
    AFTER UPDATE OF status ON procrastinate_jobs
    FOR EACH ROW
    EXECUTE PROCEDURE procrastinate_trigger_status_events_procedure_update();

DROP TRIGGER IF EXISTS procrastinate_trigger_status_events_insert ON procrastinate_jobs;
CREATE TRIGGER procrastinate_trigger_status_events_insert
    AFTER INSERT ON procrastinate_jobs
    FOR EACH ROW WHEN ((new.status = 'todo'::procrastinate_job_status))
    EXECUTE PROCEDURE procrastinate_trigger_status_events_procedure_insert();

DROP TRIGGER IF EXISTS procrastinate_trigger_scheduled_events ON procrastinate_jobs;
CREATE TRIGGER procrastinate_trigger_scheduled_events
    AFTER UPDATE OR INSERT ON procrastinate_jobs
    FOR EACH ROW WHEN ((new.scheduled_at IS NOT NULL AND new.status = 'todo'::procrastinate_job_status))
    EXECUTE PROCEDURE procrastinate_trigger_scheduled_events_procedure();

DROP TRIGGER IF EXISTS procrastinate_trigger_delete_jobs ON procrastinate_jobs;
CREATE TRIGGER procrastinate_trigger_delete_jobs
    BEFORE DELETE ON procrastinate_jobs
    FOR EACH ROW EXECUTE PROCEDURE procrastinate_unlink_periodic_defers();
