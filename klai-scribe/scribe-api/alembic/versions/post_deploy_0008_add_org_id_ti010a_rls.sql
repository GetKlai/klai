-- post_deploy_0008_add_org_id_ti010a_rls.sql
-- SPEC-TI-010A Finding A-9 -- Cat-D RLS on scribe.transcriptions
--
-- Run as the klai superuser AFTER alembic upgrade head completes:
--   ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
--     < klai-scribe/scribe-api/alembic/versions/post_deploy_0008_add_org_id_ti010a_rls.sql
--
-- Idempotent (uses CREATE OR REPLACE / IF NOT EXISTS).

BEGIN;

-- Helper function that reads the GUC set by the app layer.
-- Mirrors the pattern used in connector schema (SPEC-TI-002).
CREATE OR REPLACE FUNCTION scribe._rls_current_org_id()
RETURNS text
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
    SELECT NULLIF(current_setting('app.current_org_id', true), '')
$$;

-- Enable RLS on the table.
ALTER TABLE scribe.transcriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE scribe.transcriptions FORCE ROW LEVEL SECURITY;

-- Drop existing policy if present (idempotent re-run).
DROP POLICY IF EXISTS tenant_isolation ON scribe.transcriptions;

-- Cat-D policy: strict USING + WITH CHECK.
-- The IS NULL branch allows the reaper and any cross-org maintenance
-- operation that explicitly avoids setting the GUC (cross_org_session).
CREATE POLICY tenant_isolation ON scribe.transcriptions
    FOR ALL
    USING (
        org_id = scribe._rls_current_org_id()
        OR scribe._rls_current_org_id() IS NULL
    )
    WITH CHECK (
        org_id = scribe._rls_current_org_id()
    );

-- Grant the scribe role access to the helper function.
GRANT EXECUTE ON FUNCTION scribe._rls_current_org_id() TO scribe_api;

COMMIT;
