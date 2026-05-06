-- SPEC-TI-003-FOLLOWUP-001 AC-7: re-apply FORCE ROW LEVEL SECURITY on all
-- knowledge.* tables, restoring the hardening that the 2026-05-06 hot-fix
-- temporarily disabled (the asyncpg-pool-guc-not-shared pitfall would have
-- caused application errors with FORCE on while pg_store still grabbed pool
-- connections without the GUC).
--
-- Operator preconditions
-- ----------------------
-- 1. The companion code change is deployed: every knowledge.* SQL caller
--    threads the conn from a tenant_scoped_connection or
--    cross_org_admin_connection block. Verify by sampling
--    klai-knowledge-ingest/knowledge_ingest/pg_store.py -- every function
--    must take ``conn`` as its first non-self argument.
--
-- 2. SPEC-TI-011 has migrated knowledge-ingest off the ``klai`` superuser
--    DSN. Until then ``klai`` bypasses RLS entirely (Postgres super-user
--    semantics), so FORCE has no effect. Running this SQL before
--    SPEC-TI-011 is harmless but also delivers no isolation benefit.
--
-- Apply (operator-run, AS klai):
--   ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
--       < klai-knowledge-ingest/alembic/versions/post_deploy_dd1b439a57d0_force.sql
--
-- Verification (post-apply):
--   SELECT relname, relrowsecurity, relforcerowsecurity
--     FROM pg_class
--    WHERE relnamespace = 'knowledge'::regnamespace
--      AND relkind = 'r'
--    ORDER BY relname;
-- Every row should show relrowsecurity = t AND relforcerowsecurity = t.
--
-- Then watch VictoriaLogs for an hour for new 42501 errors:
--   service:knowledge-ingest AND message:"42501"
-- Zero hits = wiring is complete.
--
-- Rollback (if a regression surfaces):
--   ALTER TABLE knowledge.<name> NO FORCE ROW LEVEL SECURITY;
-- for the affected table, then triage the offending caller. Do NOT
-- disable RLS itself (DISABLE ROW LEVEL SECURITY) -- that would let any
-- caller without a GUC read every tenant's data.

BEGIN;

-- 1. Tables with their own org_id column (Cat-D direct policy).
ALTER TABLE knowledge.artifacts FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.entities FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.crawl_domains FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.crawl_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.crawled_pages FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.kb_config FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.org_config FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.page_links FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.parent_chunks FORCE ROW LEVEL SECURITY;

-- 2. Junction tables scoped via the artifacts FK (Cat-D subquery policy).
ALTER TABLE knowledge.artifact_entities FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.artifact_images FORCE ROW LEVEL SECURITY;
ALTER TABLE knowledge.derivations FORCE ROW LEVEL SECURITY;

-- 3. embedding_queue currently carries a permissive draft policy
-- (see post_deploy_dd1b439a57d0.sql section 4). FORCE makes the policy
-- the only path even for table owners; permissive USING(true) means it
-- still admits everything until the FK-scoped policy lands. Listed here
-- so the FORCE rollout matches the SPEC's "all 13 knowledge.* tables".
ALTER TABLE knowledge.embedding_queue FORCE ROW LEVEL SECURITY;

COMMIT;
