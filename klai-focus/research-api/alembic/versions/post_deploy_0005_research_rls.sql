-- post_deploy_0005_research_rls.sql
-- A-10: RLS helper function and Cat-D policies for research schema.
--
-- Run as `klai` superuser AFTER `alembic upgrade head` completes and
-- AFTER the research-api container restarts with the new code (which
-- calls set_tenant before every query).
--
-- Without these policies, ENABLE RLS (from migration 0005_research_rls_enable)
-- makes all tables default-deny — the research-api cannot serve any requests.
--
-- Idempotent: safe to re-run.
--
-- Finding: A-10 (audit-tenant-isolation-2026-05-05)
-- Refs: SPEC-TI-004-RLS-RESEARCH
--
-- OPERATOR STEP:
--   ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
--     < klai-focus/research-api/alembic/versions/post_deploy_0005_research_rls.sql
--   docker restart klai-core-research-api-1

BEGIN;

-- -----------------------------------------------------------------------
-- 1. Helper function: resolve-or-raise (UUID variant for research schema).
-- -----------------------------------------------------------------------
-- research schema uses tenant_id as UUID (matching portal_orgs.zitadel_org_id
-- stored as text in portal, but used as uuid here). The function reads
-- app.current_tenant_id (note: NOT app.current_org_id — research-api sets
-- a dedicated GUC to avoid namespace collision with portal-api's integer GUC).
--
-- Returns:
--   NULL  — when app.cross_org_admin='true' (explicit bypass for admin sweeps)
--   uuid  — when app.current_tenant_id is set to a valid UUID string
--   RAISE — when neither is set (fail-loud: missing tenant context)
--
-- Type-discipline: research schema uses uuid (not integer, not text).
-- See standards.md section 1 "Type-discipline" note.
CREATE OR REPLACE FUNCTION research._rls_current_org_id()
    RETURNS uuid
    LANGUAGE plpgsql
    STABLE
AS $$
DECLARE
    v_tenant text := current_setting('app.current_tenant_id', true);
    v_bypass text := current_setting('app.cross_org_admin', true);
BEGIN
    -- Explicit bypass for cross-org admin sweeps (see cross_org_session in
    -- app/core/db.py). Returns NULL so policies treat it as "match all".
    IF v_bypass = 'true' THEN
        RETURN NULL;
    END IF;

    IF v_tenant IS NULL OR v_tenant = '' THEN
        RAISE EXCEPTION
            'RLS: app.current_tenant_id is not set and app.cross_org_admin is not true. '
            'Open the session via tenant_scoped_session() for tenant work, or '
            'cross_org_session() for admin sweeps. '
            'research-api must call set_tenant(session, tenant_id) after auth.'
            USING ERRCODE = '42501';
    END IF;

    RETURN v_tenant::uuid;
END;
$$;

COMMENT ON FUNCTION research._rls_current_org_id() IS
    'Returns current tenant_id (uuid) from app.current_tenant_id, NULL if app.cross_org_admin=true, '
    'or RAISES 42501 when neither is set. Used by tenant RLS policies on research.* tables. '
    'SPEC-TI-004-RLS-RESEARCH / Finding A-10.';

-- Grant execute to the research_api role (the DB user research-api connects as).
-- If the role does not exist yet, this is a no-op comment — adjust to match prod role name.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_api') THEN
        EXECUTE 'GRANT EXECUTE ON FUNCTION research._rls_current_org_id() TO research_api';
    END IF;
END;
$$;

-- -----------------------------------------------------------------------
-- 2. Cat-D "strict" policies on all four research tables.
-- -----------------------------------------------------------------------
-- Pattern: Cat-D from standards.md section 1.
--   USING:      tenant_id = _rls_current_org_id() OR _rls_current_org_id() IS NULL
--               (IS NULL allows cross_org_session bypass)
--   WITH CHECK: tenant_id = _rls_current_org_id()
--               (no IS NULL — INSERT/UPDATE must always bind to a real tenant)
--
-- We DROP + CREATE (not ALTER POLICY) because ALTER POLICY cannot change
-- expressions in all Postgres versions and DROP+CREATE is atomic in a transaction.

-- research.notebooks
DROP POLICY IF EXISTS tenant_isolation ON research.notebooks;
CREATE POLICY tenant_isolation ON research.notebooks
    FOR ALL
    USING (
        research._rls_current_org_id() IS NULL
        OR tenant_id = research._rls_current_org_id()
    )
    WITH CHECK (tenant_id = research._rls_current_org_id());

-- research.sources
DROP POLICY IF EXISTS tenant_isolation ON research.sources;
CREATE POLICY tenant_isolation ON research.sources
    FOR ALL
    USING (
        research._rls_current_org_id() IS NULL
        OR tenant_id = research._rls_current_org_id()
    )
    WITH CHECK (tenant_id = research._rls_current_org_id());

-- research.chunks
DROP POLICY IF EXISTS tenant_isolation ON research.chunks;
CREATE POLICY tenant_isolation ON research.chunks
    FOR ALL
    USING (
        research._rls_current_org_id() IS NULL
        OR tenant_id = research._rls_current_org_id()
    )
    WITH CHECK (tenant_id = research._rls_current_org_id());

-- research.chat_messages
DROP POLICY IF EXISTS tenant_isolation ON research.chat_messages;
CREATE POLICY tenant_isolation ON research.chat_messages
    FOR ALL
    USING (
        research._rls_current_org_id() IS NULL
        OR tenant_id = research._rls_current_org_id()
    )
    WITH CHECK (tenant_id = research._rls_current_org_id());

-- Sanity check visible in psql output:
SELECT 'research RLS policies applied — _rls_current_org_id() and tenant_isolation policies ready' AS status;

COMMIT;
