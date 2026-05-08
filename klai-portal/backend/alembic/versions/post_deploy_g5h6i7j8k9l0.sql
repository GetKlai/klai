-- post_deploy_g5h6i7j8k9l0.sql
-- SPEC-PRIVACY-QUERY-SHADOW-001: telemetry schema + query_shadow table.
--
-- Run as `klai` superuser AFTER `alembic upgrade g5h6i7j8k9l0` completes.
--
-- Rationale: portal_api cannot CREATE SCHEMA (it has no CREATE privilege
-- on database `klai`) and cannot CREATE EXTENSION. Both must run under
-- the klai superuser. The companion alembic migration handles only the
-- statements portal_api can run on its owned tables.
--
-- Apply via:
--   ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
--     < post_deploy_g5h6i7j8k9l0.sql
-- Or via the wrapper:  scripts/apply_post_deploy_sql.sh g5h6i7j8k9l0
--
-- Idempotent: every statement uses IF NOT EXISTS so partial-failure
-- reruns and re-application on a freshly-restored DB are safe.

-- 1. pgvector extension (verified installed on prod 2026-05-08; this is
--    a no-op on existing Klai environments but ensures fresh-install
--    parity).
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Dedicated schema for ephemeral observational telemetry. Separate
--    schema (not `public`) because:
--      a. Different retention semantics (7d TTL vs indefinite for portal data)
--      b. Different access pattern (write-heavy from retrieval-api)
--      c. Future-proof for adding more shadow-style stores
CREATE SCHEMA IF NOT EXISTS telemetry;

-- Allow portal_api (and by transitive klai-superuser) to read/write the
-- new schema. retrieval-api already connects as klai (verified
-- 2026-05-08 in retrieval_api/config.py: portal_events_user='klai').
GRANT USAGE ON SCHEMA telemetry TO portal_api;

-- 3. Shadow-store table. The privacy contract: literal query text is
--    NEVER stored here. Only the embedding (BGE-M3 1024-dim vector) and
--    derived symbolic features (tokens, lang, has_brand, etc.).
--
--    7-day TTL is enforced by a separate cron job (Unit 7) — Postgres
--    has no native TTL, so the job runs DELETE WHERE created_at <
--    now() - interval '7 days'. The created_at index makes that scan
--    cheap.
CREATE TABLE IF NOT EXISTS telemetry.query_shadow (
    request_id     uuid PRIMARY KEY,
    org_id         text NOT NULL,
    embedding      vector(1024),
    features       jsonb NOT NULL,
    band           text,
    chunk_ids      text[],
    reranker_top1  real,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_query_shadow_created
    ON telemetry.query_shadow (created_at);
CREATE INDEX IF NOT EXISTS ix_query_shadow_org_created
    ON telemetry.query_shadow (org_id, created_at DESC);

-- portal_api inserts/reads via the gap-events + tenant self-service
-- code paths (Unit 6 + Unit 9). retrieval-api inserts via the shadow
-- writer (Unit 3) under the klai role.
GRANT SELECT, INSERT, DELETE ON telemetry.query_shadow TO portal_api;

-- No RLS on telemetry.query_shadow for v1: the table is keyed on a
-- text org_id (Zitadel ID) for retrieval-api compatibility, and the
-- read path is operator-only (no tenant-facing query). Future RLS can
-- layer on top if/when a tenant-facing surface ships. Documented in
-- SPEC § 3.2 Out-of-scope.

-- ─── REQ-12: one-time legacy cleanup of pre-existing query content ────────
--
-- These DML statements live here (not in the alembic migration) because
-- portal_retrieval_gaps is RLS-protected (Category-D, strict). Alembic
-- runs as the portal_api role without app.current_org_id set, which
-- raises 42501 against the strict policy. The klai superuser bypasses
-- RLS so this script can do the cleanup safely.
--
-- Idempotent: the WHERE clauses exclude already-redacted rows, so
-- re-runs on subsequent deploys are no-ops. Pre-flight verified
-- portal_retrieval_gaps is empty on prod (2026-05-08), so first-run
-- blast radius = 0.

UPDATE public.portal_retrieval_gaps
   SET query_text = '[REDACTED:legacy]'
 WHERE query_text NOT LIKE '[REDACTED:%'
   AND occurred_at < now() - interval '7 days';

DELETE FROM public.portal_retrieval_gaps
 WHERE occurred_at < now() - interval '30 days';

-- knowledge.retrieval_logs cleanup intentionally omitted: the table
-- does not exist on prod (verified 2026-05-08). The retrieval-log
-- lives in Redis with a 1h TTL (klai-portal/backend/app/services/
-- retrieval_log.py) — the SPEC's REQ-9 was reinterpreted to gate the
-- Redis blob's query_resolved field at write time (Unit 6) instead.
