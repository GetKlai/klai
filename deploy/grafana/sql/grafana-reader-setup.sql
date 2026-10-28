-- Create a read-only Grafana user for the portal database.
-- Run this once on klai-core-postgres-1 as a superuser.
-- Store the generated password in .env as GRAFANA_POSTGRES_PASSWORD.

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'grafana_reader') THEN
    -- Replace <generated_password> with the value from SOPS / .env
    CREATE USER grafana_reader WITH PASSWORD '<generated_password>';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE portal TO grafana_reader;
GRANT USAGE ON SCHEMA public TO grafana_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO grafana_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO grafana_reader;

-- NOTE: the ALTER DEFAULT PRIVILEGES above only covers tables created by the
-- role that ran it. Alembic creates tables as portal_api, so anything added
-- after the last run of this script is NOT readable by grafana_reader. Check
-- with the query at the bottom of this file before wiring a new alert.

-- ── SPEC-KB-015 — feedback-loop observability ────────────────────────────
--
-- Two separate reasons a plain grant is not enough here:
--
--   1. portal_feedback_events is a category-C RLS table and its SELECT policy
--      is scoped to role portal_api. grafana_reader therefore has NO
--      applicable policy, and a table with RLS enabled and no matching policy
--      returns zero rows -- silently, with a grant in place.
--   2. The table stores feedback_text, user-written free text that has no
--      business being reachable from a dashboard role.
--
-- The view solves both. It exposes only the three columns the correlation
-- alert needs, and because it is created by the superuser and is NOT
-- security_invoker, Postgres evaluates the base table's RLS as the view owner.
-- No base-table grant is widened.
CREATE OR REPLACE VIEW portal_feedback_correlation_stats AS
    SELECT org_id, correlated, occurred_at
      FROM portal_feedback_events;

GRANT SELECT ON portal_feedback_correlation_stats TO grafana_reader;

-- ── SPEC-RAG-EVAL — faithfulness + canary alerts ─────────────────────────
--
-- rag-eval-001-faithfulness-low and rag-eval-001-canary-dropped read
-- knowledge.rag_eval_results and both failed with "permission denied for
-- schema knowledge" on every evaluation since they were provisioned. USAGE on
-- the schema is a separate privilege from SELECT on the table -- granting only
-- one of the two still denies.
--
-- Granted at table level rather than schema-wide: a future table under
-- knowledge may well hold customer content, and this role should not inherit
-- access to it by accident. The table itself holds no user text -- scores,
-- timings, chunk ids, a query_id, and a meta jsonb whose only keys are
-- variant/error/errors (verified against production, 7264 rows). No RLS.
GRANT USAGE ON SCHEMA knowledge TO grafana_reader;
GRANT SELECT ON knowledge.rag_eval_results TO grafana_reader;

-- ── SPEC-PRIVACY-QUERY-SHADOW-001 — tenant-stuck-in-full alert ───────────
--
-- spec-priv-001-tenant-stuck-full asks "which orgs have been in full telemetry
-- mode for more than 14 days", which needs the moment the mode was switched.
-- That lives in portal_audit_log, a category-C RLS table whose
-- tenant_isolation_read policy is scoped to
-- org_id = current_setting('app.current_org_id') -- a GUC Grafana never sets.
-- The grant exists, so nothing errors: the read just returns zero rows, the
-- rule's IN-subquery is always empty, and the alert has never been able to
-- fire. Found 2026-08-14 by verify-alert-datasource-access.py.
--
-- Narrower than the feedback view on purpose. portal_audit_log spans every
-- audited action and its details jsonb can carry operational specifics, so this
-- exposes ONLY telemetry-mode transitions, and only three fields of them: which
-- org, when, and which level it moved to. Nothing about any other action is
-- reachable through it.
CREATE OR REPLACE VIEW portal_telemetry_mode_changes AS
    SELECT org_id,
           created_at,
           details ->> 'new_level' AS new_level
      FROM portal_audit_log
     WHERE action = 'telemetry_level_changed';

GRANT SELECT ON portal_telemetry_mode_changes TO grafana_reader;

-- Support evidence is the one feature that requires telemetry_level=full
-- (docs/architecture/support-gap-detection.md), and a downgrade purges it
-- (telemetry_level.py), so a tenant that holds support cases is in full on
-- purpose. portal_support_cases is category-D RLS and holds customer
-- conversations; this superuser-owned view exposes only which orgs hold any.
CREATE OR REPLACE VIEW portal_support_evidence_orgs
WITH (security_invoker = false, security_barrier = true) AS
    SELECT DISTINCT org_id
      FROM portal_support_cases;

GRANT SELECT ON portal_support_evidence_orgs TO grafana_reader;

-- ── Meeting recording and transcript health ─────────────────────────────
--
-- vexa_meetings is a category-D RLS table. Its SELECT policy requires the
-- app.current_org_id tenant GUC, which Grafana never sets: grafana_reader has
-- SELECT on the base table but reads zero rows (verified in production on
-- 2026-09-01: superuser 17 rows, grafana_reader 0). Granting the base table
-- again would not fix that blackhole, and bypassing its RLS would expose
-- meeting URLs, titles, transcript text, and user identifiers.
--
-- This superuser-owned, non-security-invoker view exposes only the four
-- derived/operational fields the alert needs. The view owner evaluates the
-- base table's RLS; grafana_reader never receives a wider base-table grant.
CREATE OR REPLACE VIEW portal_meeting_transcript_health
WITH (security_invoker = false, security_barrier = true) AS
    SELECT status,
           created_at,
           updated_at AS outcome_at,
           (
               NULLIF(btrim(transcript_text), '') IS NOT NULL
               OR jsonb_array_length(COALESCE(transcript_segments, '[]'::jsonb)) > 0
           ) AS has_transcript
      FROM vexa_meetings;

REVOKE ALL ON portal_meeting_transcript_health FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO grafana_reader;
GRANT SELECT ON portal_meeting_transcript_health TO grafana_reader;

-- ── Crawl job health ─────────────────────────────────────────────────────
--
-- The crawl-rules.yaml rules read knowledge.crawl_jobs, which has FORCE ROW
-- LEVEL SECURITY (alembic 0002) and holds the crawl config, with cookies and
-- login selectors. A base-table grant would read zero rows and expose that
-- config, so the rules read this superuser-owned view instead. It exposes the
-- connector id (the one key of config), status, page counts, the
-- epoch-seconds updated_at, the error_summary reason code, and per job:
--
--   fetch_total            number of fetch_outcomes
--   crawler_side_failures  outcomes raised by OUR request to crawl4ai rather
--                          than answered by the target site: a 4xx or 5xx
--                          httpx.HTTPStatusError, or a connect/read timeout.
--                          A site's own 4xx/5xx is recorded page-level with
--                          error_type 'crawl4ai:<reason>' and is not counted.
--                          429 is not counted either: it is the site asking
--                          for a slower pace.
--   top_signature          the most frequent error_type:status_code
--   top_signature_share    its share of fetch_total
--
-- New columns go last so CREATE OR REPLACE keeps working on an existing view.
-- 30 days of jobs is the horizon: it keeps the jsonb expansion cheap and covers
-- the longest window any rule reads (7 days 30 hours).
-- Added 2026-10-28 after six nights of crawls that answered errors (2026-10-23
-- to 2026-10-28) raised no alert.
CREATE OR REPLACE VIEW knowledge.crawl_job_health
WITH (security_invoker = false, security_barrier = true) AS
    SELECT j.org_id,
           j.kb_slug,
           j.status,
           j.pages_total,
           j.pages_done,
           j.updated_at,
           j.error_summary ->> 'reason' AS reason,
           j.config ->> 'connector_id' AS connector_id,
           o.fetch_total,
           o.crawler_side_failures,
           t.signature AS top_signature,
           round(t.n::numeric / NULLIF(o.fetch_total, 0), 3) AS top_signature_share
      FROM knowledge.crawl_jobs j
     CROSS JOIN LATERAL (
           SELECT count(*)::integer AS fetch_total,
                  (count(*) FILTER (
                       WHERE coalesce(
                                 (x ->> 'error_type' = 'httpx.HTTPStatusError'
                                  AND (x ->> 'reason_code' IN ('http_4xx', 'auth_error')
                                       OR coalesce((x ->> 'status_code')::integer, 0) >= 500))
                                 OR x ->> 'error_type' IN
                                    ('httpx.ConnectError', 'httpx.ReadTimeout', 'httpx.ConnectTimeout'),
                                 false)
                  ))::integer AS crawler_side_failures
             FROM jsonb_array_elements(j.fetch_outcomes) AS x
           ) o
      LEFT JOIN LATERAL (
           SELECT (x ->> 'error_type') || ':' || coalesce(x ->> 'status_code', 'none') AS signature,
                  count(*) AS n
             FROM jsonb_array_elements(j.fetch_outcomes) AS x
            WHERE x ->> 'error_type' IS NOT NULL
            GROUP BY 1
            ORDER BY 2 DESC, 1
            LIMIT 1
           ) t ON true
     WHERE j.updated_at >= date_part('epoch', now() - interval '30 days');

REVOKE ALL ON knowledge.crawl_job_health FROM PUBLIC;
GRANT USAGE ON SCHEMA knowledge TO grafana_reader;
GRANT SELECT ON knowledge.crawl_job_health TO grafana_reader;

-- crawl_jobs_silent needs to know which sources are SUPPOSED to crawl every
-- day, which only the portal knows. portal_connectors holds config and
-- encrypted credentials and portal_knowledge_bases is FORCE-RLS, so this view
-- exposes only the connector id, its knowledge base slug and its creation time,
-- for enabled, active web crawlers of an org-scoped knowledge base whose cron
-- schedule runs every day (day-of-month, month and weekday all '*'). Weekly and
-- unscheduled sources are left out on purpose: a missing job is normal there.
CREATE OR REPLACE VIEW portal_scheduled_crawlers
WITH (security_invoker = false, security_barrier = true) AS
    SELECT c.id::text AS connector_id,
           kb.slug AS kb_slug,
           date_part('epoch', c.created_at) AS created_at
      FROM portal_connectors c
      JOIN portal_knowledge_bases kb ON kb.id = c.kb_id
     WHERE c.connector_type = 'web_crawler'
       AND c.is_enabled
       AND c.state = 'active'
       AND kb.owner_type = 'org'
       AND c.schedule ~ '^\S+\s+\S+\s+\*\s+\*\s+\*\s*$';

REVOKE ALL ON portal_scheduled_crawlers FROM PUBLIC;
GRANT SELECT ON portal_scheduled_crawlers TO grafana_reader;

-- Verify after running (must return a non-zero count, not an error and not 0):
--   SET ROLE grafana_reader;
--   SELECT count(*) FROM portal_feedback_correlation_stats;
--   SELECT count(*) FROM portal_meeting_transcript_health;
--
-- Audit which granted tables still read as empty for this role -- an RLS
-- policy scoped to another role looks identical to "no data" from Grafana:
--   SELECT table_name FROM information_schema.role_table_grants
--    WHERE grantee = 'grafana_reader' AND privilege_type = 'SELECT';
