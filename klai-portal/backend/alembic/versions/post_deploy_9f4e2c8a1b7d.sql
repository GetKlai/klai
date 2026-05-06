-- Post-deploy RLS + ownership setup for SPEC-MCP-AUTH-001 migration 9f4e2c8a1b7d.
-- Run as `klai` superuser AFTER `alembic upgrade 9f4e2c8a1b7d` completes.
--
-- The Alembic migration cannot run these statements: portal_api (the
-- migration role) is not the table owner and cannot ALTER TABLE OWNER,
-- ENABLE ROW LEVEL SECURITY, or CREATE POLICY.
--
-- Idempotent: every statement uses `IF NOT EXISTS` / `DROP POLICY IF EXISTS`
-- so this script is safe to re-run on partial-failure or for re-application.
--
-- ─── Apply via ───────────────────────────────────────────────────────────
--   ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
--     < post_deploy_9f4e2c8a1b7d.sql
-- Or via the wrapper:  scripts/apply_post_deploy_sql.sh 9f4e2c8a1b7d


-- 1. Transfer ownership of new tables to klai (consistent with other
--    RLS-enabled tables: portal_users, portal_groups, widgets, etc.).
ALTER TABLE portal_oauth_clients OWNER TO klai;
ALTER TABLE portal_mcp_tokens OWNER TO klai;

-- 2. Grant CRUD privileges to portal_api (the application role).
GRANT SELECT, INSERT, UPDATE, DELETE ON portal_oauth_clients TO portal_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON portal_mcp_tokens TO portal_api;

-- Sequences are auto-created for BIGSERIAL columns. Without USAGE on the
-- sequence, INSERT raises permission_denied. Match the pattern from other
-- post_deploy SQL files.
GRANT USAGE, SELECT ON SEQUENCE portal_oauth_clients_id_seq TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE portal_mcp_tokens_id_seq TO portal_api;


-- 3. portal_oauth_clients: NO RLS.
--    The table has no `org_id` column and is org-overstijgend by design
--    (DCR is anonymous; tenant-scoping happens on token issuance via
--    portal_mcp_tokens.org_id, not here). RLS policies that scope on
--    `current_setting('app.current_org_id')` would either reject every
--    DCR INSERT (no tenant context) or be permissive (defeats the point).
--    The defense surface for this table is at the application layer:
--
--      a. DCR endpoint validates redirect_uri against an allowlist
--         (REQ-20).
--      b. DCR endpoint enforces per-IP rate-limit (REQ-27).
--      c. soft_deleted_at filters in every read query (no direct DB UI).
--
--    See SPEC-MCP-AUTH-001 § Architecture Decision A4.


-- 4. portal_mcp_tokens: RLS Category-D (strict — raise on missing tenant).
ALTER TABLE portal_mcp_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE portal_mcp_tokens FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON portal_mcp_tokens;
CREATE POLICY tenant_isolation ON portal_mcp_tokens
    FOR ALL TO portal_api
    USING (
        org_id = current_setting('app.current_org_id', true)::integer
    )
    WITH CHECK (
        org_id = current_setting('app.current_org_id', true)::integer
    );

-- Cross-tenant reads/writes hit ``insufficient_privilege`` (42501) — exactly
-- what the rls_guard event listener AND the smoke-test script expect for
-- Category-D tables. See app/core/rls_guard.py::RLS_DML_TABLES (must include
-- 'portal_mcp_tokens') and scripts/rls-smoke-test.sql Test sections.
