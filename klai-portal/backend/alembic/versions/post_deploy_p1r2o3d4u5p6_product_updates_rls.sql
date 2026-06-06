BEGIN;

ALTER TABLE product_updates ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_updates FORCE ROW LEVEL SECURITY;
ALTER TABLE product_update_reads ENABLE ROW LEVEL SECURITY;
ALTER TABLE product_update_reads FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS product_updates_select ON product_updates;
CREATE POLICY product_updates_select ON product_updates
    FOR SELECT
    USING (true);

DROP POLICY IF EXISTS product_updates_insert ON product_updates;
CREATE POLICY product_updates_insert ON product_updates
    FOR INSERT
    WITH CHECK (current_setting('app.cross_org_admin', true) = 'true');

-- product_update_reads is a category-D pure-tenant table (per-user read
-- state, always queried inside tenant_scoped_session). It MUST fail loud on
-- missing tenant context, so the org scope uses _rls_current_org_id()
-- (defined by post_deploy_rls_raise_on_missing_context.sql, applied first by
-- deploy-portal-api.sh) instead of the inline NULLIF pattern reserved for
-- category-A auth-seed tables. See klai-portal/backend/AGENTS.md MUST rule 5.
DROP POLICY IF EXISTS product_update_reads_select ON product_update_reads;
CREATE POLICY product_update_reads_select ON product_update_reads
    FOR SELECT
    USING (
        org_id = _rls_current_org_id()
        AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
    );

DROP POLICY IF EXISTS product_update_reads_insert ON product_update_reads;
CREATE POLICY product_update_reads_insert ON product_update_reads
    FOR INSERT
    WITH CHECK (
        org_id = _rls_current_org_id()
        AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
    );

COMMIT;
