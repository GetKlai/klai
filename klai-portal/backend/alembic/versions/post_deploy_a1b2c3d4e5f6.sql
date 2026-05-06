-- SPEC-TI-010C (B-6): RLS policy for portal_users_librechat_index
-- Run as klai superuser AFTER alembic upgrade head completes.
-- Category D: strict org isolation, cross-org bypass via explicit NULL GUC.

BEGIN;

ALTER TABLE portal_users_librechat_index ENABLE ROW LEVEL SECURITY;

-- Allow portal_api to read/write only rows belonging to the current tenant.
-- Cross-org bootstrap (bootstrap_librechat_index.py) runs as klai superuser
-- which bypasses RLS, so the insert path is safe without a permissive INSERT.
DROP POLICY IF EXISTS portal_users_librechat_index_tenant_isolation ON portal_users_librechat_index;
CREATE POLICY portal_users_librechat_index_tenant_isolation
    ON portal_users_librechat_index
    AS RESTRICTIVE
    USING (
        (current_setting('app.current_org_id', true)::integer IS NULL)
        OR org_id = current_setting('app.current_org_id', true)::integer
    );

COMMIT;
