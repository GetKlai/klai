-- post_deploy_ti005_tenant_isolation_hygiene.sql
-- SPEC-TI-005 -- portal-api RLS hygiene batch (findings A-1 to A-6)
--
-- Run as `klai` superuser AFTER portal-api is deployed:
--   ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" \
--       < klai-portal/backend/alembic/versions/post_deploy_ti005_tenant_isolation_hygiene.sql
-- Then restart portal-api:
--   docker restart klai-core-portal-api-1
--
-- Idempotent: safe to re-run (DROP POLICY IF EXISTS + CREATE).
-- The portal_api role cannot run these statements (not the table owner).
--
-- Finding refs: A-1, A-2, A-3, A-4, A-5, A-6
-- Audit: reports/audit-tenant-isolation-2026-05-05/report.md

BEGIN;

-- ============================================================
-- A-1: Add explicit WITH CHECK to portal_users + portal_connectors
--      Category-A (auth-seed): USING keeps IS-NULL branch so
--      pre-auth lookups (e.g. _get_caller_org, connector callbacks)
--      succeed when no tenant context is set. WITH CHECK does NOT
--      have the IS-NULL branch -- any INSERT/UPDATE must bind to a
--      real org_id even in a context-free session.
--
--      Pattern: standards.md section 2 (Cat-A explicit WITH CHECK).
--      Anti-pattern prevented: USING reused as WITH CHECK means the
--      IS-NULL branch allows cross-org INSERT (Finding A-1).
-- ============================================================

-- portal_users — Cat-A AUTH-SEED
-- HOTFIX 2026-05-06 (round 2): USING uses inline NULLIF pattern with literal
-- "IS NULL" in the expression, NOT the helper-function `_rls_current_org_id()`.
--
-- Why not the helper:
--   `_rls_current_org_id()` is fail-loud (RAISES 42501 when GUC not set).
--   That is correct for Cat-D tenant tables where every caller MUST have
--   `set_tenant()` first. But `portal_users` is Cat-A AUTH-SEED — `/api/me`
--   and `_get_caller_org` look up (org, user) BY zitadel_user_id BEFORE
--   they know which tenant to set. Calling `_rls_current_org_id()` from
--   that lookup raises 42501 → every authenticated request returns 500.
--
-- Why the inline `NULLIF(...) = ''` IS NULL form:
--   1. Permissive on missing GUC → /api/me lookup works.
--   2. Contains the literal substring "IS NULL" → satisfies the existing
--      lifespan guard `assert_portal_users_rls_ready()` (substring match).
--   3. WITH CHECK keeps A-1 hardening — every INSERT/UPDATE must carry an
--      explicit org_id matching the calling tenant context.
--
-- See pitfall: rls-policy-shape-must-match-lifespan-assert (HIGH).
DROP POLICY IF EXISTS tenant_isolation ON portal_users;
CREATE POLICY tenant_isolation ON portal_users
    FOR ALL TO portal_api
    USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
        OR NULLIF(current_setting('app.current_org_id', true), '') IS NULL
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
    );

-- portal_connectors — also AUTH-SEED-adjacent
-- OAuth callbacks resolve the connector row by callback-token BEFORE setting
-- the tenant context. Same Cat-A reasoning as portal_users above.
DROP POLICY IF EXISTS tenant_isolation ON portal_connectors;
CREATE POLICY tenant_isolation ON portal_connectors
    FOR ALL TO portal_api
    USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
        OR NULLIF(current_setting('app.current_org_id', true), '') IS NULL
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
    );

-- ============================================================
-- A-2: portal_group_memberships -- enable RLS + subquery policy
--      Junction table has no own org_id; tenant scope derives from
--      the parent portal_groups row (Cat-D subquery pattern).
--      The original migration created a policy that post_deploy_rls
--      confirmed did not land on prod. We create it here idempotently.
-- ============================================================

ALTER TABLE portal_group_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE portal_group_memberships FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON portal_group_memberships;
CREATE POLICY tenant_isolation ON portal_group_memberships
    FOR ALL TO portal_api
    USING (
        group_id IN (
            SELECT id FROM portal_groups
            WHERE org_id = _rls_current_org_id()
               OR _rls_current_org_id() IS NULL
        )
    )
    WITH CHECK (
        group_id IN (
            SELECT id FROM portal_groups
            WHERE org_id = _rls_current_org_id()
        )
    );

-- ============================================================
-- A-3: ENABLE + FORCE on partner_api_keys and partner_api_key_kb_access
--      These tables had ENABLE/FORCE only in the migration docstring
--      ("operator note"), not in executable DDL. Running here adds the
--      mechanical guarantee. Policies already exist from
--      post_deploy_rls_raise_on_missing_context.sql -- this only ensures
--      RLS is switched on at the engine level.
-- ============================================================

ALTER TABLE partner_api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_api_keys FORCE ROW LEVEL SECURITY;

ALTER TABLE partner_api_key_kb_access ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_api_key_kb_access FORCE ROW LEVEL SECURITY;

-- ============================================================
-- A-4: FORCE ROW LEVEL SECURITY on 4 tables that had ENABLE but not FORCE.
--      Without FORCE, owner role (klai superuser) bypasses RLS.
--      Operator scripts, ad-hoc psql, and alembic itself run as klai --
--      adding FORCE closes that defense-in-depth gap.
-- ============================================================

ALTER TABLE portal_feedback_events FORCE ROW LEVEL SECURITY;
ALTER TABLE widgets FORCE ROW LEVEL SECURITY;
ALTER TABLE widget_kb_access FORCE ROW LEVEL SECURITY;
ALTER TABLE tenant_lifecycle_events FORCE ROW LEVEL SECURITY;

-- ============================================================
-- A-5: Tighten INSERT WITH CHECK on Cat-C audit/event tables
--      Replace WITH CHECK (true) with a guard that blocks writes
--      where org_id does not match the session's app.current_org_id
--      when a tenant context IS set. Fire-and-forget paths that run
--      without tenant context continue to work via the '' branch.
--
--      Pattern: standards.md section 2 + finding A-5.
--      Attack prevented: a session with app.current_org_id=A doing
--      emit_event(org_id=B,...) would write a B-tagged audit row.
-- ============================================================

-- portal_audit_log -- tighten "tenant_isolation_write" (migration 83a82cc61aee).
DROP POLICY IF EXISTS tenant_isolation_write ON portal_audit_log;
CREATE POLICY tenant_isolation_write ON portal_audit_log
    FOR INSERT TO portal_api
    WITH CHECK (
        current_setting('app.current_org_id', true) = ''
        OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
    );

-- product_events -- tighten "tenant_write" (migration 6dd868123a4e).
DROP POLICY IF EXISTS tenant_write ON product_events;
CREATE POLICY tenant_write ON product_events
    FOR INSERT TO portal_api
    WITH CHECK (
        current_setting('app.current_org_id', true) = ''
        OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
    );

-- portal_feedback_events -- tighten "feedback_events_insert_policy" (migration b6c7d8e9f0a1).
DROP POLICY IF EXISTS feedback_events_insert_policy ON portal_feedback_events;
CREATE POLICY feedback_events_insert_policy ON portal_feedback_events
    FOR INSERT TO portal_api
    WITH CHECK (
        current_setting('app.current_org_id', true) = ''
        OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
    );

-- tenant_lifecycle_events -- tighten "tenant_lifecycle_events_insert"
-- (post_deploy_7e2d3c1a9b8f.sql). The deprovisioning orchestrator writes
-- within set_tenant(state.org_id) context (deprovisioning_steps.py:750),
-- so we require an exact org_id_snapshot match when context is set.
DROP POLICY IF EXISTS tenant_lifecycle_events_insert ON tenant_lifecycle_events;
CREATE POLICY tenant_lifecycle_events_insert ON tenant_lifecycle_events
    FOR INSERT TO portal_api
    WITH CHECK (
        current_setting('app.current_org_id', true) = ''
        OR org_id_snapshot = NULLIF(current_setting('app.current_org_id', true), '')::integer
    );

-- ============================================================
-- Sanity check (visible in psql --echo-queries output):
-- ============================================================
SELECT 'SPEC-TI-005 RLS hygiene batch applied' AS status;

COMMIT;
