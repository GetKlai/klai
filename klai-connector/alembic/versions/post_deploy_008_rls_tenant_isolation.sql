-- post_deploy_008_rls_tenant_isolation.sql — HOTFIX 2026-05-06
-- Run as `klai` superuser.
--
-- Hotfix rationale:
--   PostgreSQL does NOT support function overloading by return type alone.
--   The original SPEC-TI-002 SQL assumed it could create a `text`-returning
--   variant of `_rls_current_org_id()` next to portal-api's `integer`
--   variant. CREATE OR REPLACE FUNCTION rejects this with
--     "ERROR: cannot change return type of existing function"
--   Rename the connector helper to `_rls_current_org_text()` and update the
--   policies to call the renamed function.
--
-- This SQL file is the prod-applied fix; a follow-up commit will sync the
-- repo copy at klai-connector/alembic/versions/post_deploy_008_rls_tenant_isolation.sql.
-- Idempotent: safe to re-run.

BEGIN;

-- ----------------------------------------------------------------------------
-- 1. Helper function: _rls_current_org_text() RETURNS text
--
-- connector schema uses org_id VARCHAR(255) — Zitadel resourceowner string.
-- Renamed from `_rls_current_org_id()` to avoid clash with portal-api's
-- existing integer-returning variant (Postgres has no return-type overload).
-- STABLE: same result within a transaction for the same session settings.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION _rls_current_org_text()
    RETURNS text
    LANGUAGE plpgsql
    STABLE
AS $$
DECLARE
    v_org    text := current_setting('app.current_org_id', true);
    v_bypass text := current_setting('app.cross_org_admin', true);
BEGIN
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

COMMENT ON FUNCTION _rls_current_org_text() IS
    'Returns the current tenant org_id (text/varchar) from app.current_org_id GUC. '
    'Returns NULL when app.cross_org_admin=true (cross-org bypass). '
    'RAISES ERRCODE 42501 (insufficient_privilege) when neither GUC is set. '
    'Used by RLS policies on connector.connectors and connector.sync_runs. '
    'SPEC-TI-002 / finding A-7. Renamed from _rls_current_org_id() (hotfix 2026-05-06).';

-- Grant EXECUTE to the connector service role if it exists.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'connector_api') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION _rls_current_org_text() TO connector_api';
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2. Policy on connector.connectors (Cat-D, fail-loud).
-- ----------------------------------------------------------------------------
DROP POLICY IF EXISTS tenant_isolation ON connector.connectors;
CREATE POLICY tenant_isolation ON connector.connectors
    FOR ALL
    USING      (org_id = _rls_current_org_text() OR _rls_current_org_text() IS NULL)
    WITH CHECK (org_id = _rls_current_org_text());

-- ----------------------------------------------------------------------------
-- 3. Policy on connector.sync_runs (Cat-D, fail-loud).
--    sync_runs.org_id is nullable (legacy rows from pre-006 may be NULL).
-- ----------------------------------------------------------------------------
DROP POLICY IF EXISTS tenant_isolation ON connector.sync_runs;
CREATE POLICY tenant_isolation ON connector.sync_runs
    FOR ALL
    USING      (org_id = _rls_current_org_text() OR _rls_current_org_text() IS NULL)
    WITH CHECK (org_id = _rls_current_org_text());

SELECT
    'post_deploy_008 applied (HOTFIX) — _rls_current_org_text() created, '
    'tenant_isolation policies installed on connector.connectors and connector.sync_runs'
    AS status;

COMMIT;
