-- EMERGENCY ROLLBACK for post_deploy_rls_raise_on_missing_context.sql.
--
-- Run as `klai` superuser via:
--     docker exec -i <postgres-container> psql -U klai -d klai \
--         -v ON_ERROR_STOP=1 < post_deploy_rls_rollback_to_nullif_pattern.sql
--
-- Reverts every category-D table to the legacy
--     USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer)
-- pattern — which silently filters rows when tenant context is missing
-- instead of raising. Use this ONLY when a code regression is making the
-- strict policies block legitimate traffic and you cannot quickly ship a
-- fix.
--
-- After rollback, the _rls_current_org_id() function is dropped to leave
-- the DB in a clean pre-migration state. cross_org_session() in portal-api
-- keeps working — it sets a session variable nobody reads, which is a
-- harmless no-op.
--
-- Re-applying the forward migration later is safe (DROP POLICY IF EXISTS).

BEGIN;

-- ---------------------------------------------------------------------------
-- Restore original policies on every category-D table.
-- Ordering mirrors post_deploy_rls_raise_on_missing_context.sql so an
-- operator can diff the two files side-by-side.
-- ---------------------------------------------------------------------------

-- portal_knowledge_bases
DROP POLICY IF EXISTS tenant_isolation ON portal_knowledge_bases;
CREATE POLICY tenant_isolation ON portal_knowledge_bases
    FOR ALL TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- portal_groups
DROP POLICY IF EXISTS tenant_isolation ON portal_groups;
CREATE POLICY tenant_isolation ON portal_groups
    FOR ALL TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- portal_group_products
DROP POLICY IF EXISTS tenant_isolation ON portal_group_products;
CREATE POLICY tenant_isolation ON portal_group_products
    FOR ALL TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- portal_group_kb_access — originally scoped via parent KB
DROP POLICY IF EXISTS tenant_isolation ON portal_group_kb_access;
CREATE POLICY tenant_isolation ON portal_group_kb_access
    FOR ALL TO portal_api
    USING (kb_id IN (
        SELECT id FROM portal_knowledge_bases
        WHERE org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer
    ));

-- portal_kb_tombstones
DROP POLICY IF EXISTS tenant_isolation ON portal_kb_tombstones;
CREATE POLICY tenant_isolation ON portal_kb_tombstones
    FOR ALL TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- portal_user_kb_access
DROP POLICY IF EXISTS tenant_isolation ON portal_user_kb_access;
CREATE POLICY tenant_isolation ON portal_user_kb_access
    FOR ALL TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- portal_retrieval_gaps
DROP POLICY IF EXISTS tenant_isolation ON portal_retrieval_gaps;
CREATE POLICY tenant_isolation ON portal_retrieval_gaps
    FOR ALL TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- portal_taxonomy_nodes — originally scoped via parent KB
DROP POLICY IF EXISTS tenant_isolation ON portal_taxonomy_nodes;
CREATE POLICY tenant_isolation ON portal_taxonomy_nodes
    FOR ALL TO portal_api
    USING (kb_id IN (
        SELECT id FROM portal_knowledge_bases
        WHERE org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer
    ));

-- portal_taxonomy_proposals — originally scoped via parent KB
DROP POLICY IF EXISTS tenant_isolation ON portal_taxonomy_proposals;
CREATE POLICY tenant_isolation ON portal_taxonomy_proposals
    FOR ALL TO portal_api
    USING (kb_id IN (
        SELECT id FROM portal_knowledge_bases
        WHERE org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer
    ));

-- portal_user_products
DROP POLICY IF EXISTS tenant_isolation ON portal_user_products;
CREATE POLICY tenant_isolation ON portal_user_products
    FOR ALL TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- vexa_meetings — per-cmd
DROP POLICY IF EXISTS tenant_read ON vexa_meetings;
CREATE POLICY tenant_read ON vexa_meetings
    FOR SELECT TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

DROP POLICY IF EXISTS tenant_write ON vexa_meetings;
CREATE POLICY tenant_write ON vexa_meetings
    FOR INSERT TO portal_api
    WITH CHECK (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

DROP POLICY IF EXISTS tenant_update ON vexa_meetings;
CREATE POLICY tenant_update ON vexa_meetings
    FOR UPDATE TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

DROP POLICY IF EXISTS tenant_delete ON vexa_meetings;
CREATE POLICY tenant_delete ON vexa_meetings
    FOR DELETE TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- partner_api_keys — per-cmd (SELECT stays permissive)
DROP POLICY IF EXISTS partner_select ON partner_api_keys;
CREATE POLICY partner_select ON partner_api_keys
    FOR SELECT TO portal_api
    USING (true);

DROP POLICY IF EXISTS partner_insert ON partner_api_keys;
CREATE POLICY partner_insert ON partner_api_keys
    FOR INSERT TO portal_api
    WITH CHECK (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

DROP POLICY IF EXISTS partner_update ON partner_api_keys;
CREATE POLICY partner_update ON partner_api_keys
    FOR UPDATE TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

DROP POLICY IF EXISTS partner_delete ON partner_api_keys;
CREATE POLICY partner_delete ON partner_api_keys
    FOR DELETE TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- partner_api_key_kb_access — scoped via parent
DROP POLICY IF EXISTS kb_access_select ON partner_api_key_kb_access;
CREATE POLICY kb_access_select ON partner_api_key_kb_access
    FOR SELECT TO portal_api
    USING (true);

DROP POLICY IF EXISTS kb_access_insert ON partner_api_key_kb_access;
CREATE POLICY kb_access_insert ON partner_api_key_kb_access
    FOR INSERT TO portal_api
    WITH CHECK (EXISTS (
        SELECT 1 FROM partner_api_keys p
        WHERE p.id = partner_api_key_id
          AND p.org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer
    ));

DROP POLICY IF EXISTS kb_access_update ON partner_api_key_kb_access;
CREATE POLICY kb_access_update ON partner_api_key_kb_access
    FOR UPDATE TO portal_api
    USING (EXISTS (
        SELECT 1 FROM partner_api_keys p
        WHERE p.id = partner_api_key_id
          AND p.org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer
    ));

DROP POLICY IF EXISTS kb_access_delete ON partner_api_key_kb_access;
CREATE POLICY kb_access_delete ON partner_api_key_kb_access
    FOR DELETE TO portal_api
    USING (EXISTS (
        SELECT 1 FROM partner_api_keys p
        WHERE p.id = partner_api_key_id
          AND p.org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer
    ));

-- widgets (SELECT stays permissive)
DROP POLICY IF EXISTS widgets_insert ON widgets;
CREATE POLICY widgets_insert ON widgets
    FOR INSERT TO portal_api
    WITH CHECK (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

DROP POLICY IF EXISTS widgets_update ON widgets;
CREATE POLICY widgets_update ON widgets
    FOR UPDATE TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

DROP POLICY IF EXISTS widgets_delete ON widgets;
CREATE POLICY widgets_delete ON widgets
    FOR DELETE TO portal_api
    USING (org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer);

-- widget_kb_access — scoped via parent widget
DROP POLICY IF EXISTS widget_kb_access_insert ON widget_kb_access;
CREATE POLICY widget_kb_access_insert ON widget_kb_access
    FOR INSERT TO portal_api
    WITH CHECK (EXISTS (
        SELECT 1 FROM widgets w
        WHERE w.id = widget_kb_access.widget_id
          AND w.org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer
    ));

DROP POLICY IF EXISTS widget_kb_access_delete ON widget_kb_access;
CREATE POLICY widget_kb_access_delete ON widget_kb_access
    FOR DELETE TO portal_api
    USING (EXISTS (
        SELECT 1 FROM widgets w
        WHERE w.id = widget_kb_access.widget_id
          AND w.org_id = (NULLIF(current_setting('app.current_org_id', true), ''))::integer
    ));

-- ---------------------------------------------------------------------------
-- Drop the strict-mode helper function. It is no longer referenced by any
-- policy after this script runs. The function's only callers are the policy
-- expressions we just replaced; dropping it is safe.
-- ---------------------------------------------------------------------------

DROP FUNCTION IF EXISTS _rls_current_org_id();

SELECT 'RLS rollback complete — policies restored to NULLIF pattern, _rls_current_org_id() dropped' AS status;

COMMIT;
