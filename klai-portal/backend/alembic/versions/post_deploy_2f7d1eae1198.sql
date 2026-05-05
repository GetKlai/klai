-- Post-deploy RLS setup for SPEC-SEC-PORTAL-RLS-001 migration 2f7d1eae1198.
-- Run as `klai` superuser after `alembic upgrade 2f7d1eae1198` completes.
--
-- Why post-deploy and not in the migration itself:
-- The Alembic migration runs as the `portal_api` role; that role is NOT the
-- owner of `portal_join_requests` (the table was created by `klai` superuser
-- in an earlier RLS-pattern migration). PostgreSQL refuses
-- `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and `CREATE POLICY` from any
-- role except the table owner — same class as
-- `alembic-cannot-drop-non-portal_api-tables` in
-- `.claude/rules/klai/pitfalls/process-rules.md` (extended in this PR to
-- cover ENABLE / FORCE ROW LEVEL SECURITY too). Running these statements
-- inside the migration crash-loops portal-api on startup until manually
-- recovered.
--
-- Category A (auth-seed) policy: the admin token-based approve flow in
-- `app/api/auth_join.py` looks up the join request by approval_token BEFORE
-- any tenant context is resolved. The IS NULL permissive branch lets that
-- pre-auth lookup succeed; admin/join_requests.py runs after `set_tenant`
-- has fired and gets strict org_id = T isolation.
--
-- Idempotent: safe to re-run. ALTER TABLE ... ENABLE is a no-op when
-- already enabled. DROP POLICY IF EXISTS + CREATE POLICY rebuilds cleanly.

ALTER TABLE portal_join_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE portal_join_requests FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON portal_join_requests;

CREATE POLICY tenant_isolation ON portal_join_requests
    USING (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::int
        OR NULLIF(current_setting('app.current_org_id', true), '') IS NULL
    )
    WITH CHECK (
        org_id = NULLIF(current_setting('app.current_org_id', true), '')::int
    );
