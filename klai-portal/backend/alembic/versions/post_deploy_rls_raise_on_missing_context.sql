-- RLS hardening: raise on missing tenant context instead of silently filtering.
--
-- Run as `klai` superuser. The Alembic migration role (`portal_api`) is not
-- the policy owner and cannot CREATE OR REPLACE FUNCTION / ALTER POLICY.
-- Idempotent: safe to re-run.
--
-- Background
-- ----------
-- The previous policy pattern was:
--     USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
--
-- When app.current_org_id is not set (e.g. fresh AsyncSessionLocal without
-- set_tenant, or a pooled connection that lost its SET because the session
-- wasn't pinned), NULLIF returns NULL, the cast returns NULL, and
--     org_id = NULL
-- evaluates to NULL → the policy filters the row out. For SELECT that's a
-- silent empty result. For UPDATE/DELETE it's a silent 0-row no-op.
--
-- New pattern: a SECURITY INVOKER function that raises when the context is
-- missing, with an explicit opt-in bypass for cross-org admin work.
--
-- Scope: PURE-TENANT tables only (category D)
-- ----------------
-- Not every RLS-scoped table is a candidate for the strict policy.
-- Three tables are intentionally LEFT on the old permissive-on-missing
-- pattern because their access flows predate tenant context:
--
--   - portal_users:     /api/me and _get_caller_org resolve the tenant
--                       by selecting portal_users BY zitadel_user_id,
--                       which must succeed before set_tenant can fire.
--   - portal_connectors: internal /connectors/* callbacks load the row
--                       by id before they can derive org_id.
--   - widgets.SELECT / partner_api_keys.SELECT / *_kb_access.SELECT:
--                       public/pre-auth widget config endpoints.
--
-- Three tables have a permissive INSERT policy by design and this
-- script does NOT touch their INSERT path:
--
--   - portal_audit_log, product_events, portal_feedback_events:
--     fire-and-forget raw-SQL INSERTs in audit.py / events.py that
--     intentionally run without tenant context.
--
-- Deployment order
-- ----------------
-- 1. Deploy portal-api code that uses tenant_scoped_session /
--    cross_org_session AND has `get_effective_products` self-healing
--    tenant context AND `rescore_open_gaps` calling set_tenant.
-- 2. Run this script (as klai superuser).
-- 3. Smoke-test: /api/me, /internal/knowledge-feature-check, admin UI.
-- The new code is compatible with BOTH the old and new policies (it
-- always sets app.current_org_id or app.cross_org_admin). Running this
-- SQL before the code deploy breaks endpoints that still rely on the
-- inline NULLIF fallback — get_effective_products notably.

-- Atomic: wrap everything in a single transaction so any error rolls
-- back all policy changes. Without BEGIN/COMMIT a partial failure would
-- leave some tables on the new function and others on the old NULLIF trick.
BEGIN;

-- ----------------------------------------------------------------------
-- 1. Helper function: resolve-or-raise.
-- ----------------------------------------------------------------------
-- Returns:
--   - NULL when app.cross_org_admin='true' (explicit bypass — cross_org_session)
--   - integer org_id when app.current_org_id is set (tenant_scoped_session / set_tenant)
--
-- Raises (ERRCODE 42501 insufficient_privilege) when neither is set. This
-- replaces the old silent-filter behaviour so any code path missing tenant
-- context fails loudly instead of returning empty results or no-op DML.
--
-- The function is STABLE (same result within a transaction given same
-- settings) so the planner can cache it across rows in a query.
CREATE OR REPLACE FUNCTION _rls_current_org_id()
    RETURNS integer
    LANGUAGE plpgsql
    STABLE
AS $$
DECLARE
    v_org     text := current_setting('app.current_org_id', true);
    v_bypass  text := current_setting('app.cross_org_admin', true);
BEGIN
    -- Explicit bypass for cross-org admin sweeps (see cross_org_session in
    -- app/core/database.py). Returns NULL so policies can treat it as "match all".
    IF v_bypass = 'true' THEN
        RETURN NULL;
    END IF;

    IF v_org IS NULL OR v_org = '' THEN
        RAISE EXCEPTION
            'RLS: app.current_org_id is not set and app.cross_org_admin is not true. '
            'Open the session via tenant_scoped_session() for tenant work, or '
            'cross_org_session() for admin sweeps.'
            USING ERRCODE = '42501';
    END IF;

    RETURN v_org::integer;
END;
$$;

COMMENT ON FUNCTION _rls_current_org_id() IS
    'Returns the current tenant org_id from app.current_org_id, NULL if app.cross_org_admin=true, '
    'or RAISES 42501 when neither is set. Used by tenant RLS policies instead of inline '
    'current_setting() to enforce fail-loud tenant context.';

GRANT EXECUTE ON FUNCTION _rls_current_org_id() TO portal_api;

-- ----------------------------------------------------------------------
-- 2. Per-table policies migrated to use _rls_current_org_id().
-- ----------------------------------------------------------------------
-- Policies use the pattern:
--     USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
-- The IS NULL branch allows the cross-org bypass; otherwise we scope by org.
--
-- We DROP and re-CREATE because ALTER POLICY cannot change the expression in
-- all PG versions and DROP+CREATE is atomic in a transaction.

-- portal_knowledge_bases
DROP POLICY IF EXISTS tenant_isolation ON portal_knowledge_bases;
CREATE POLICY tenant_isolation ON portal_knowledge_bases
    FOR ALL TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- portal_groups
DROP POLICY IF EXISTS tenant_isolation ON portal_groups;
CREATE POLICY tenant_isolation ON portal_groups
    FOR ALL TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- portal_group_products
DROP POLICY IF EXISTS tenant_isolation ON portal_group_products;
CREATE POLICY tenant_isolation ON portal_group_products
    FOR ALL TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- portal_group_memberships has NO RLS policy currently (verified 2026-04-21
-- in pg_policies). Membership rows inherit their tenant scope from the
-- parent group via FK; there's no org_id column to scope on. Skipped.

-- portal_group_kb_access — scoped via parent KB's org_id (no direct org_id)
DROP POLICY IF EXISTS tenant_isolation ON portal_group_kb_access;
CREATE POLICY tenant_isolation ON portal_group_kb_access
    FOR ALL TO portal_api
    USING (
        _rls_current_org_id() IS NULL
        OR kb_id IN (
            SELECT id FROM portal_knowledge_bases
            WHERE org_id = _rls_current_org_id()
        )
    );

-- portal_kb_tombstones
DROP POLICY IF EXISTS tenant_isolation ON portal_kb_tombstones;
CREATE POLICY tenant_isolation ON portal_kb_tombstones
    FOR ALL TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- portal_user_kb_access
DROP POLICY IF EXISTS tenant_isolation ON portal_user_kb_access;
CREATE POLICY tenant_isolation ON portal_user_kb_access
    FOR ALL TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- portal_retrieval_gaps
DROP POLICY IF EXISTS tenant_isolation ON portal_retrieval_gaps;
CREATE POLICY tenant_isolation ON portal_retrieval_gaps
    FOR ALL TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- portal_taxonomy_nodes — scoped via parent KB's org_id (no direct org_id column)
DROP POLICY IF EXISTS tenant_isolation ON portal_taxonomy_nodes;
CREATE POLICY tenant_isolation ON portal_taxonomy_nodes
    FOR ALL TO portal_api
    USING (
        _rls_current_org_id() IS NULL
        OR kb_id IN (
            SELECT id FROM portal_knowledge_bases
            WHERE org_id = _rls_current_org_id()
        )
    );

-- portal_taxonomy_proposals — same scoping pattern
DROP POLICY IF EXISTS tenant_isolation ON portal_taxonomy_proposals;
CREATE POLICY tenant_isolation ON portal_taxonomy_proposals
    FOR ALL TO portal_api
    USING (
        _rls_current_org_id() IS NULL
        OR kb_id IN (
            SELECT id FROM portal_knowledge_bases
            WHERE org_id = _rls_current_org_id()
        )
    );

-- portal_user_products
DROP POLICY IF EXISTS tenant_isolation ON portal_user_products;
CREATE POLICY tenant_isolation ON portal_user_products
    FOR ALL TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- portal_connectors — AUTH-SEED category. Internal webhook callbacks
-- (e.g. /internal/connectors/{id}/sync-complete) load the connector row
-- via `db.get(PortalConnector, id)` BEFORE they can call set_tenant —
-- there's no tenant context in the incoming request. Policy stays
-- permissive-on-missing (existing pattern) so those lookups succeed.
-- Any tenant-scoped code path still has set_tenant in place, so the
-- org_id equality branch enforces isolation.

-- portal_users — AUTH-SEED category. /api/me and _get_caller_org must
-- look up (org, user) by zitadel_user_id BEFORE they know which tenant
-- to set. Policy stays permissive-on-missing (existing pattern).

-- vexa_meetings — per-cmd policies
DROP POLICY IF EXISTS tenant_read ON vexa_meetings;
CREATE POLICY tenant_read ON vexa_meetings
    FOR SELECT TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

DROP POLICY IF EXISTS tenant_write ON vexa_meetings;
CREATE POLICY tenant_write ON vexa_meetings
    FOR INSERT TO portal_api
    WITH CHECK (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

DROP POLICY IF EXISTS tenant_update ON vexa_meetings;
CREATE POLICY tenant_update ON vexa_meetings
    FOR UPDATE TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

DROP POLICY IF EXISTS tenant_delete ON vexa_meetings;
CREATE POLICY tenant_delete ON vexa_meetings
    FOR DELETE TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- partner_api_keys — per-cmd
DROP POLICY IF EXISTS partner_select ON partner_api_keys;
CREATE POLICY partner_select ON partner_api_keys
    FOR SELECT TO portal_api
    USING (true);  -- permissive: widget session token validation runs without tenant context

DROP POLICY IF EXISTS partner_insert ON partner_api_keys;
CREATE POLICY partner_insert ON partner_api_keys
    FOR INSERT TO portal_api
    WITH CHECK (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

DROP POLICY IF EXISTS partner_update ON partner_api_keys;
CREATE POLICY partner_update ON partner_api_keys
    FOR UPDATE TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

DROP POLICY IF EXISTS partner_delete ON partner_api_keys;
CREATE POLICY partner_delete ON partner_api_keys
    FOR DELETE TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- partner_api_key_kb_access — per-cmd
DROP POLICY IF EXISTS kb_access_select ON partner_api_key_kb_access;
CREATE POLICY kb_access_select ON partner_api_key_kb_access
    FOR SELECT TO portal_api
    USING (true);  -- permissive: widget config lookup

DROP POLICY IF EXISTS kb_access_insert ON partner_api_key_kb_access;
CREATE POLICY kb_access_insert ON partner_api_key_kb_access
    FOR INSERT TO portal_api
    WITH CHECK (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

DROP POLICY IF EXISTS kb_access_update ON partner_api_key_kb_access;
CREATE POLICY kb_access_update ON partner_api_key_kb_access
    FOR UPDATE TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

DROP POLICY IF EXISTS kb_access_delete ON partner_api_key_kb_access;
CREATE POLICY kb_access_delete ON partner_api_key_kb_access
    FOR DELETE TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- widgets — per-cmd (widgets_select stays permissive for public config endpoint)
DROP POLICY IF EXISTS widgets_insert ON widgets;
CREATE POLICY widgets_insert ON widgets
    FOR INSERT TO portal_api
    WITH CHECK (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

DROP POLICY IF EXISTS widgets_update ON widgets;
CREATE POLICY widgets_update ON widgets
    FOR UPDATE TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

DROP POLICY IF EXISTS widgets_delete ON widgets;
CREATE POLICY widgets_delete ON widgets
    FOR DELETE TO portal_api
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id());

-- widget_kb_access — per-cmd (select stays permissive)
DROP POLICY IF EXISTS widget_kb_access_insert ON widget_kb_access;
CREATE POLICY widget_kb_access_insert ON widget_kb_access
    FOR INSERT TO portal_api
    WITH CHECK (EXISTS (
        SELECT 1 FROM widgets w
        WHERE w.id = widget_id
          AND (_rls_current_org_id() IS NULL OR w.org_id = _rls_current_org_id())
    ));

DROP POLICY IF EXISTS widget_kb_access_delete ON widget_kb_access;
CREATE POLICY widget_kb_access_delete ON widget_kb_access
    FOR DELETE TO portal_api
    USING (EXISTS (
        SELECT 1 FROM widgets w
        WHERE w.id = widget_id
          AND (_rls_current_org_id() IS NULL OR w.org_id = _rls_current_org_id())
    ));

-- ----------------------------------------------------------------------
-- 3. portal_audit_log / product_events / portal_feedback_events stay as-is.
--    INSERT policies remain permissive; read policies stay tenant-scoped with
--    the old inline NULLIF pattern because the write paths INTENTIONALLY
--    skip tenant context (fire-and-forget raw SQL inserts in audit.py and
--    events.py).
-- ----------------------------------------------------------------------

-- Sanity check (no-op in production, visible in psql output):
SELECT 'RLS upgrade applied — _rls_current_org_id function and policies refreshed' AS status;

COMMIT;
