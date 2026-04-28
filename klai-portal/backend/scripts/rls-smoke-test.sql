-- Post-migration smoke test for strict RLS policies.
-- Run as portal_api role (the app's DB user) to exercise the policies
-- exactly as the application does.
--
-- Usage:
--     ssh core-01 "docker exec -i klai-core-postgres-1 psql -U portal_api -d klai" < rls-smoke-test.sql
--
-- Exit with non-zero via \set ON_ERROR_STOP on — any unexpected outcome
-- raises and the shell script wrapping this sees the failure.

\set ON_ERROR_STOP on
\pset footer off

SELECT '=== Test 1: auth-seed table without tenant context — expect success ===' AS test;
-- portal_users keeps the permissive-on-missing pattern so auth flows
-- can resolve (user -> org) before set_tenant has fired.
SELECT COUNT(*) AS users_no_tenant FROM portal_users WHERE zitadel_user_id = '__nonexistent__';

SELECT '=== Test 2: pure-tenant table without tenant context — expect ERROR 42501 ===' AS test;
-- This block MUST raise. We swallow it so the rest of the script runs;
-- a caller script should check the full output for the expected error.
DO $$
BEGIN
    BEGIN
        PERFORM COUNT(*) FROM portal_knowledge_bases;
        RAISE EXCEPTION 'RLS SMOKE FAILURE: SELECT on portal_knowledge_bases without tenant context did not raise';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'OK: portal_knowledge_bases raised insufficient_privilege as expected';
    END;
END $$;

SELECT '=== Test 3: pure-tenant WITH tenant context — expect scoped rows ===' AS test;
SELECT set_config('app.current_org_id', '1', false);
SELECT
    COUNT(*) AS kbs_org_1
FROM portal_knowledge_bases;

SELECT '=== Test 4: change tenant to another org — expect different count ===' AS test;
SELECT set_config('app.current_org_id', '8', false);
SELECT
    COUNT(*) AS kbs_org_8,
    COALESCE(array_agg(slug) FILTER (WHERE slug IS NOT NULL), ARRAY[]::varchar[]) AS slugs
FROM portal_knowledge_bases;

SELECT '=== Test 5: explicit cross-org bypass — expect all rows across tenants ===' AS test;
SELECT set_config('app.current_org_id', '', false);
SELECT set_config('app.cross_org_admin', 'true', false);
SELECT COUNT(*) AS kbs_all_orgs FROM portal_knowledge_bases;

SELECT '=== Test 6: vexa_meetings UPDATE without tenant — expect ERROR 42501 ===' AS test;
SELECT set_config('app.cross_org_admin', '', false);
SELECT set_config('app.current_org_id', '', false);
DO $$
BEGIN
    BEGIN
        UPDATE vexa_meetings SET status = status WHERE id = '00000000-0000-0000-0000-000000000000';
        RAISE EXCEPTION 'RLS SMOKE FAILURE: UPDATE on vexa_meetings without tenant context did not raise';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'OK: vexa_meetings UPDATE raised insufficient_privilege as expected';
    END;
END $$;

SELECT '=== Test 7 (SPEC-SEC-TENANT-001 REQ-6.1): portal_group_kb_access without tenant context — expect ERROR 42501 ===' AS test;
-- portal_group_kb_access is a category-D pure-tenant junction table
-- (membership-of-group ↔ access-to-kb). REQ-6.1 verifies — does not
-- assume — that RLS is the working defence-in-depth layer for the
-- offboard cross-tenant scenario. A direct SELECT without tenant GUC
-- MUST raise insufficient_privilege.
DO $$
BEGIN
    BEGIN
        PERFORM COUNT(*) FROM portal_group_kb_access;
        RAISE EXCEPTION 'RLS SMOKE FAILURE: SELECT on portal_group_kb_access without tenant context did not raise';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'OK: portal_group_kb_access raised insufficient_privilege as expected (REQ-6.1)';
    END;
END $$;

-- SPEC-SEC-TENANT-001 REQ-6.2: portal_group_memberships intentionally
-- carries NO RLS policy. Membership rows inherit their tenant via the
-- parent group's FK (PortalGroup.org_id). The code-layer guard for the
-- offboard cross-tenant scenario is the org-scoped subselect in
-- klai-portal/backend/app/api/admin/users.py::offboard_user (REQ-1) —
-- pinned by tests/test_admin_users.py::
-- test_offboard_user_does_not_wipe_other_org_memberships (REQ-5.1).
-- Do NOT add an RLS policy on portal_group_memberships under this SPEC.
-- If the table ever gains an org_id column, that is a separate
-- schema-change SPEC + RLS-upgrade migration.

SELECT '=== Test 8: cleanup ===' AS test;
SELECT set_config('app.current_org_id', '', false);
SELECT set_config('app.cross_org_admin', '', false);

SELECT 'RLS smoke test complete — all assertions passed' AS result;

