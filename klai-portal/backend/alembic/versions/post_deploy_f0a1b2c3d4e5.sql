-- Post-deploy RLS and ownership setup for SPEC-WIDGET-002 migration f0a1b2c3d4e5.
-- Run as `klai` superuser after `alembic upgrade f0a1b2c3d4e5` completes.
-- The Alembic migration itself cannot run these statements because the
-- migration role (`portal_api`) is not the table owner and cannot execute
-- ALTER TABLE ... OWNER, ENABLE ROW LEVEL SECURITY, or CREATE POLICY.
--
-- Idempotent: safe to re-run.

-- 1. Transfer ownership of the new tables to klai (consistent with
--    partner_api_keys and other RLS-protected tables).
ALTER TABLE widgets OWNER TO klai;
ALTER TABLE widget_kb_access OWNER TO klai;

-- 2. Grant CRUD privileges to portal_api (the application role).
GRANT SELECT, INSERT, UPDATE, DELETE ON widgets TO portal_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON widget_kb_access TO portal_api;

-- 3. Enable row-level security on both tables.
ALTER TABLE widgets ENABLE ROW LEVEL SECURITY;
ALTER TABLE widget_kb_access ENABLE ROW LEVEL SECURITY;

-- 4. RLS policies on `widgets`. All commands tenant-scoped on
--    app.current_org_id (set per request by set_tenant()).
DROP POLICY IF EXISTS widgets_select ON widgets;
CREATE POLICY widgets_select ON widgets
    FOR SELECT TO portal_api
    USING (org_id = current_setting('app.current_org_id', true)::integer);

DROP POLICY IF EXISTS widgets_insert ON widgets;
CREATE POLICY widgets_insert ON widgets
    FOR INSERT TO portal_api
    WITH CHECK (org_id = current_setting('app.current_org_id', true)::integer);

DROP POLICY IF EXISTS widgets_update ON widgets;
CREATE POLICY widgets_update ON widgets
    FOR UPDATE TO portal_api
    USING (org_id = current_setting('app.current_org_id', true)::integer);

DROP POLICY IF EXISTS widgets_delete ON widgets;
CREATE POLICY widgets_delete ON widgets
    FOR DELETE TO portal_api
    USING (org_id = current_setting('app.current_org_id', true)::integer);

-- 5. RLS policies on `widget_kb_access`. Junction inherits tenant scope
--    from its parent widget's org_id.
DROP POLICY IF EXISTS widget_kb_access_select ON widget_kb_access;
CREATE POLICY widget_kb_access_select ON widget_kb_access
    FOR SELECT TO portal_api
    USING (EXISTS (
        SELECT 1 FROM widgets w
        WHERE w.id = widget_kb_access.widget_id
          AND w.org_id = current_setting('app.current_org_id', true)::integer
    ));

DROP POLICY IF EXISTS widget_kb_access_insert ON widget_kb_access;
CREATE POLICY widget_kb_access_insert ON widget_kb_access
    FOR INSERT TO portal_api
    WITH CHECK (EXISTS (
        SELECT 1 FROM widgets w
        WHERE w.id = widget_kb_access.widget_id
          AND w.org_id = current_setting('app.current_org_id', true)::integer
    ));

DROP POLICY IF EXISTS widget_kb_access_delete ON widget_kb_access;
CREATE POLICY widget_kb_access_delete ON widget_kb_access
    FOR DELETE TO portal_api
    USING (EXISTS (
        SELECT 1 FROM widgets w
        WHERE w.id = widget_kb_access.widget_id
          AND w.org_id = current_setting('app.current_org_id', true)::integer
    ));

-- 6. Drop the widget-specific and soft-delete columns on partner_api_keys
--    if the Alembic migration was unable to (it runs as portal_api, which
--    is not the table owner). Safe to re-run.
ALTER TABLE partner_api_keys DROP CONSTRAINT IF EXISTS ck_partner_api_keys_integration_type;
ALTER TABLE partner_api_keys DROP CONSTRAINT IF EXISTS uq_partner_api_keys_widget_id;
ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS integration_type;
ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS widget_id;
ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS widget_config;
ALTER TABLE partner_api_keys DROP COLUMN IF EXISTS active;
