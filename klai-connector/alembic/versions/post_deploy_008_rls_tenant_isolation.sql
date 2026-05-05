-- post_deploy_008_rls_tenant_isolation.sql
-- Run as `klai` superuser. The Alembic migration role (`connector_api`)
-- cannot CREATE OR REPLACE FUNCTION or CREATE POLICY.
-- Idempotent: safe to re-run.
--
-- SPEC-TI-002 / audit finding A-7 (tenant-isolation audit 2026-05-05)
--
-- What this does
-- --------------
-- 1. Creates _rls_current_org_id() RETURNS text in the public schema
--    (shared with portal-api's integer variant — they coexist because
--     the connector schema's org_id is text/varchar, not integer).
--    The function is overloaded by return type; if a text variant already
--    exists it is replaced with CREATE OR REPLACE.
--    - Returns NULL  when app.cross_org_admin = 'true'  (cross_org bypass)
--    - Returns the org_id text  when app.current_org_id is set
--    - RAISES 42501  when neither GUC is set  (fail-loud Cat-D policy)
--
-- 2. Creates Cat-D tenant_isolation policies on connector.connectors and
--    connector.sync_runs using the helper function.
--
--    USING:      org_id = _rls_current_org_id() OR _rls_current_org_id() IS NULL
--    WITH CHECK: org_id = _rls_current_org_id()  -- no IS NULL branch intentionally
--
--    The WITH CHECK deliberately lacks the IS NULL branch. An INSERT or UPDATE
--    must always carry an explicit org_id matching the calling tenant context.
--    Cross-org sessions may SELECT across tenants but NOT INSERT/UPDATE without
--    an explicit org_id. This prevents "orphan row" bugs. See standards.md §1.
--
-- Deployment order
-- ----------------
-- 1. Alembic migration 008_rls_tenant_isolation runs first (ENABLE + FORCE).
-- 2. Run this file as klai superuser.
-- 3. Restart klai-connector so the updated session-helpers are active.
-- 4. Smoke-test: connector sync trigger, list sync runs.
--
-- Operator command
-- ----------------
-- ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
--   < klai-connector/alembic/versions/post_deploy_008_rls_tenant_isolation.sql

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Helper function: _rls_current_org_id() RETURNS text
--
-- connector schema uses org_id VARCHAR(255) — Zitadel resourceowner string.
-- RETURNS text (not integer) — different from portal-api's integer variant.
-- PostgreSQL allows function overloading by return type when used in
-- USING/WITH CHECK expressions directly (no cast ambiguity).
-- STABLE: same result within a transaction for the same session settings.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _rls_current_org_id()
    RETURNS text
    LANGUAGE plpgsql
    STABLE
AS $$
DECLARE
    v_org    text := current_setting('app.current_org_id', true);
    v_bypass text := current_setting('app.cross_org_admin', true);
BEGIN
    -- Explicit bypass for cross-org admin sweeps (cross_org_session() in
    -- klai-connector/app/core/database.py). Returns NULL so USING policies
    -- evaluate TRUE for every row (via IS NULL branch).
    IF v_bypass = 'true' THEN
        RETURN NULL;
    END IF;

    IF v_org IS NULL OR v_org = '' THEN
        RAISE EXCEPTION
            'RLS: app.current_org_id is not set and app.cross_org_admin is not true. '
            'Use tenant_scoped_session(org_id) for tenant work, or '
            'cross_org_session() for admin sweeps. SPEC-TI-002 finding A-7.'
            USING ERRCODE = '42501';
    END IF;

    RETURN v_org;
END;
$$;

COMMENT ON FUNCTION _rls_current_org_id() IS
    'Returns the current tenant org_id (text/varchar) from app.current_org_id GUC. '
    'Returns NULL when app.cross_org_admin=true (cross-org bypass). '
    'RAISES ERRCODE 42501 (insufficient_privilege) when neither GUC is set. '
    'Used by RLS policies on connector.connectors and connector.sync_runs. '
    'SPEC-TI-002 / finding A-7.';

-- Grant EXECUTE to the connector service role if it exists.
-- The role name may differ between environments; adjust as needed.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'connector_api') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION _rls_current_org_id() TO connector_api';
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2. Policy on connector.connectors
--    Cat-D (strict, fail-loud on missing context).
--    USING: allows cross-org bypass (IS NULL branch from helper returning NULL)
--           and per-tenant equality otherwise.
--    WITH CHECK: no NULL branch — INSERT/UPDATE always require explicit org_id.
-- ----------------------------------------------------------------------------
DROP POLICY IF EXISTS tenant_isolation ON connector.connectors;
CREATE POLICY tenant_isolation ON connector.connectors
    FOR ALL
    USING      (org_id = _rls_current_org_id() OR _rls_current_org_id() IS NULL)
    WITH CHECK (org_id = _rls_current_org_id());

-- ----------------------------------------------------------------------------
-- 3. Policy on connector.sync_runs
--    Same Cat-D pattern. sync_runs.org_id is nullable (legacy rows from
--    before migration 006 may have org_id IS NULL). Those rows are
--    effectively invisible to per-tenant queries, but visible under
--    cross_org_session() — needed by the lifespan startup sweep and reaper.
-- ----------------------------------------------------------------------------
DROP POLICY IF EXISTS tenant_isolation ON connector.sync_runs;
CREATE POLICY tenant_isolation ON connector.sync_runs
    FOR ALL
    USING      (org_id = _rls_current_org_id() OR _rls_current_org_id() IS NULL)
    WITH CHECK (org_id = _rls_current_org_id());

-- Sanity marker (visible in psql output, no side-effects in prod):
SELECT
    'post_deploy_008 applied — _rls_current_org_id() (text variant) created, '
    'tenant_isolation policies installed on connector.connectors and connector.sync_runs'
    AS status;

COMMIT;
