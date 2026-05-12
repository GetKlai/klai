-- SPEC-PORTAL-PRICING-PER-USER-001 Phase 1 post-deploy SQL.
--
-- Applied as the ``klai`` superuser AFTER ``alembic upgrade f66c546c12eb``
-- completes successfully. portal_api cannot ENABLE / FORCE RLS or
-- CREATE POLICY on a table even if it owns the table — same class as
-- alembic-cannot-drop-non-portal_api-tables in process-rules.md.
--
-- Idempotent: every statement uses IF NOT EXISTS / DROP IF EXISTS so
-- re-running on a partially-applied database is safe.
--
-- Run from core-01 once after the deploy lands:
--     ./scripts/apply_post_deploy_sql.sh post_deploy_f66c546c12eb.sql
-- or manually:
--     docker exec -i klai-core-postgres-1 sh -c \
--         'psql -U $POSTGRES_USER -d $POSTGRES_DB' \
--         < post_deploy_f66c546c12eb.sql

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Schema-qualified RLS helper for the billing domain.
--
--    Distinct from portal-api's public._rls_current_org_id() (which
--    returns integer) AND distinct from knowledge._rls_current_org_id()
--    (which returns text). Schema-qualification is the prescribed fix
--    in the postgres-no-return-type-overload pitfall — Postgres does NOT
--    support function overloading by return type alone, so co-existing
--    same-named functions in the same schema breaks production.
--
--    STABLE is correct: within a single statement, current_setting()'s
--    value does not change. ``security definer`` is deliberately NOT
--    used — the function should resolve under the caller's privileges
--    so a superuser bypass remains visible in the policy plan.
-- ---------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS billing;

CREATE OR REPLACE FUNCTION billing._rls_current_org_id() RETURNS integer AS $$
    SELECT NULLIF(current_setting('app.current_org_id', true), '')::integer;
$$ LANGUAGE sql STABLE;

-- Explicit grants for portal_api. Postgres' default ACL grants USAGE on
-- new schemas and EXECUTE on new functions to PUBLIC, so this is
-- defensive: if a future hardening pass runs
--     REVOKE EXECUTE ON ALL FUNCTIONS ... FROM PUBLIC;
-- the RLS policy's USING clause would silently return NULL (no rows
-- visible) instead of failing loud. Explicit grants make portal_api's
-- read path immune to that drift. ``knowledge._rls_current_org_id()``
-- (created earlier under the same pattern) relies on the default PUBLIC
-- grants; we tighten this one because the migration is fresh and the
-- pattern is now established here for any future Cat-D helpers.
GRANT USAGE ON SCHEMA billing TO portal_api;
GRANT EXECUTE ON FUNCTION billing._rls_current_org_id() TO portal_api;

-- ---------------------------------------------------------------------------
-- 2. Enable + force RLS on portal_user_seat_history.
--
--    ENABLE = policies apply to non-owner sessions.
--    FORCE  = policies apply to the table owner too (portal_api). Without
--             FORCE, portal_api could read across tenants — defeating
--             the point. Mirrors the Cat-D pattern from SPEC-TI-005.
-- ---------------------------------------------------------------------------

ALTER TABLE portal_user_seat_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE portal_user_seat_history FORCE  ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- 3. The tenant_isolation policy.
--
--    Category D (strict): the helper raises if app.current_org_id is
--    NULL/empty (via NULLIF + ::integer coerce). Callers MUST set
--    SET LOCAL app.current_org_id = T before issuing any SELECT/INSERT/
--    UPDATE/DELETE. Phase 5 prorate-billing query is the canonical
--    consumer; Phase 1 portal-api only writes via the trigger from
--    inside an already-tenant-bound transaction.
--
--    CREATE POLICY does NOT support IF NOT EXISTS, so we DROP first.
-- ---------------------------------------------------------------------------

DROP POLICY IF EXISTS tenant_isolation ON portal_user_seat_history;

CREATE POLICY tenant_isolation ON portal_user_seat_history
    USING      (org_id = billing._rls_current_org_id())
    WITH CHECK (org_id = billing._rls_current_org_id());

COMMIT;
