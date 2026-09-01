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

-- Verify after running (must return a non-zero count, not an error and not 0):
--   SET ROLE grafana_reader;
--   SELECT count(*) FROM portal_feedback_correlation_stats;
--   SELECT count(*) FROM portal_meeting_transcript_health;
--
-- Audit which granted tables still read as empty for this role -- an RLS
-- policy scoped to another role looks identical to "no data" from Grafana:
--   SELECT table_name FROM information_schema.role_table_grants
--    WHERE grantee = 'grafana_reader' AND privilege_type = 'SELECT';
