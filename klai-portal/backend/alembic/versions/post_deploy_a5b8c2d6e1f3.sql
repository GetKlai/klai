-- post_deploy_a5b8c2d6e1f3.sql
-- SPEC-MCP-RETRIEVAL-001 Phase 2: partial index on portal_retrieval_gaps.caller_client_id.
--
-- Run as klai superuser (NOT portal_api) so the CONCURRENTLY index build is
-- allowed regardless of pool state. Partial index keeps LibreChat-only
-- queries (the dominant path) unaffected — the index only stores rows where
-- caller_client_id IS NOT NULL.
--
-- CONCURRENTLY cannot run inside an explicit transaction; this script must
-- be executed outside a BEGIN/COMMIT wrapper. The deploy-runbook calls it
-- via `psql -v ON_ERROR_STOP=1` which preserves implicit single-statement
-- transactions per statement.
--
-- Idempotent via IF NOT EXISTS so repeated runs (e.g. retried deploys)
-- are no-ops after the first success.

CREATE INDEX CONCURRENTLY IF NOT EXISTS portal_retrieval_gaps_caller_client_id_idx
    ON portal_retrieval_gaps (caller_client_id)
    WHERE caller_client_id IS NOT NULL;
