-- Post-deploy RLS and ownership setup for SPEC-INFRA-TENANT-DELETE-001 migration 7e2d3c1a9b8f.
-- Run as `klai` superuser after `alembic upgrade 7e2d3c1a9b8f` completes.
-- The Alembic migration itself cannot run these statements because the
-- migration role (`portal_api`) is not the table owner and cannot execute
-- ALTER TABLE ... OWNER, ENABLE ROW LEVEL SECURITY, or CREATE POLICY.
--
-- Idempotent: safe to re-run.
--
-- Category-classification (per .claude/rules/klai/projects/portal-security.md):
-- tenant_lifecycle_events is Category-B-with-platform-restriction:
--   * INSERT permissive — orchestrator inserts during deprovisioning when
--     the org is in a transitional state; we do NOT want RLS to block that
--     write (the row's whole purpose is to outlive the org delete).
--   * SELECT restricted — only platform-admin queries (caller_org.slug ==
--     settings.platform_org_slug) need to read this audit table.
--   * UPDATE/DELETE restricted to platform-admin (audit row is append-only;
--     erasure under GDPR is the only legitimate DELETE and that is a manual
--     superuser action documented in the runbook).

-- 1. Transfer ownership to klai (consistent with widgets, partner_api_keys, etc.).
ALTER TABLE tenant_lifecycle_events OWNER TO klai;

-- 2. Grant CRUD privileges to portal_api (the application role).
GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_lifecycle_events TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE tenant_lifecycle_events_id_seq TO portal_api;

-- 3. Enable row-level security.
ALTER TABLE tenant_lifecycle_events ENABLE ROW LEVEL SECURITY;

-- 4. Policies.
-- INSERT: permissive. The deprovisioning orchestrator (in step 16
-- _finalize_postgres_delete) inserts the audit row inside the same transaction
-- that hard-deletes portal_orgs. The session at that point has the
-- deprovisioning org's tenant context set, so a strict tenant-match policy
-- would still allow it — but we keep it permissive so a future
-- system-context insert (e.g. provisioning audit, where the org doesn't yet
-- exist) does not need a special bypass.
DROP POLICY IF EXISTS tenant_lifecycle_events_insert ON tenant_lifecycle_events;
CREATE POLICY tenant_lifecycle_events_insert ON tenant_lifecycle_events
    FOR INSERT TO portal_api
    WITH CHECK (true);

-- SELECT: restricted to platform org. The platform_org_slug ('getklai') is
-- not stored in DB, so the policy uses a sentinel: a session is "platform"
-- when app.is_platform_admin GUC is set to '1' by the auth dependency for
-- platform-admin endpoints. For all other sessions (regular tenants), SELECT
-- returns no rows.
--
-- Implementation note: portal-api MUST set
--   SET LOCAL app.is_platform_admin = '1'
-- in `_get_caller_org` (or a wrapper) when caller_org.slug ==
-- settings.platform_org_slug. Until that wiring lands, SELECT returns empty
-- for ALL tenants — which is the safe default (no audit leak).
DROP POLICY IF EXISTS tenant_lifecycle_events_select ON tenant_lifecycle_events;
CREATE POLICY tenant_lifecycle_events_select ON tenant_lifecycle_events
    FOR SELECT TO portal_api
    USING (current_setting('app.is_platform_admin', true) = '1');

-- UPDATE: forbidden via RLS (no policy = no rows match).
-- DELETE: forbidden via RLS (no policy = no rows match). GDPR erasure goes
-- via klai superuser per the runbook.

-- 5. Verification query (operator can run after applying):
--   SELECT polname, polcmd FROM pg_policies WHERE tablename = 'tenant_lifecycle_events';
--   Expected: tenant_lifecycle_events_insert (a), tenant_lifecycle_events_select (r).
