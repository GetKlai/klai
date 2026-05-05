-- Post-deploy RLS setup for SPEC-SEC-CONNECTOR-RLS-001 migration 008.
-- Run as the table-owner role (klai superuser) AFTER `alembic upgrade head`
-- completes. The migration body is intentionally a no-op; this file
-- carries the actual DDL.
--
-- Why post-deploy and not in the migration:
-- ALTER TABLE ... ENABLE / FORCE ROW LEVEL SECURITY and CREATE POLICY
-- require the table owner. Even if klai-connector currently runs alembic
-- as klai today, future role-splits would re-trigger the
-- `alembic-cannot-drop-non-portal_api-tables` crash-loop. Keeping
-- owner-required DDL out of migrations is the canonical klai pattern
-- (see post_deploy_2f7d1eae1198.sql, post_deploy_7e2d3c1a9b8f.sql in
-- klai-portal/backend/alembic/versions/).
--
-- Apply via `scripts/apply_post_deploy_sql.sh` or manually:
--   psql -U klai -d klai -f post_deploy_008.sql
--
-- Idempotent: safe to re-run.
--
-- Policy shape — Category D (strict) per SPEC-SEC-CONNECTOR-RLS-001:
--   * USING — SELECT/UPDATE/DELETE matches when org_id equals the
--     ``app.current_org_id`` GUC, OR when ``app.cross_org_admin`` is set
--     to '1'. The cross-org branch is the legitimate escape hatch for
--     the lifespan crash-recovery, the SyncRunReaper periodic sweep, and
--     the scheduler bootstrap that loads all tenant schedules at
--     startup. Set EXCLUSIVELY via ``cross_org_session()`` in
--     ``app/core/database.py``.
--   * WITH CHECK — INSERT/UPDATE writes must satisfy ``org_id = GUC``,
--     UNLESS ``cross_org_admin`` is set. Stops a request-scoped handler
--     from writing a row with someone else's org_id.
--   * No IS NULL permissive branch on USING (would allow NULL-org_id
--     reads to leak cross-tenant). Historical pre-migration-006 rows in
--     ``connector.sync_runs`` carry NULL ``org_id`` and are
--     intentionally invisible to tenant-scoped queries; only the
--     ``cross_org_admin`` path can see them.
--
-- FORCE ROW LEVEL SECURITY — keeps the owner role subject to the policy
-- at runtime, so the no-role-split decision is safe.

-- ---------------------------------------------------------------------------
-- connector.connectors
-- ---------------------------------------------------------------------------
-- ``org_id`` is NOT NULL (per SPEC-SEC-TENANT-001 REQ-7.x). Every row
-- has a tenant; the policy is unambiguous.

ALTER TABLE connector.connectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector.connectors FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON connector.connectors;

CREATE POLICY tenant_isolation ON connector.connectors
    USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')
        OR current_setting('app.cross_org_admin', true) = '1'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')
        OR current_setting('app.cross_org_admin', true) = '1'
    );

-- ---------------------------------------------------------------------------
-- connector.sync_runs
-- ---------------------------------------------------------------------------
-- ``org_id`` is nullable (migration 006 added the column without
-- backfill). Historical NULL rows are invisible to tenant-scoped queries
-- by design — they never had a binding tenant and surface only for the
-- reaper / lifespan cross-org sweeps.

ALTER TABLE connector.sync_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE connector.sync_runs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON connector.sync_runs;

CREATE POLICY tenant_isolation ON connector.sync_runs
    USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')
        OR current_setting('app.cross_org_admin', true) = '1'
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')
        OR current_setting('app.cross_org_admin', true) = '1'
    );
