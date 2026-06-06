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

DROP POLICY IF EXISTS product_update_reads_select ON product_update_reads;
CREATE POLICY product_update_reads_select ON product_update_reads
    FOR SELECT
    USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
        AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
    );

DROP POLICY IF EXISTS product_update_reads_insert ON product_update_reads;
CREATE POLICY product_update_reads_insert ON product_update_reads
    FOR INSERT
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
        AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
    );

COMMIT;
