-- post_deploy_b2d3e4f5a6c7_portal_templates_rls_with_check.sql
--
-- REQ-3 (Finding C-1): add explicit WITH CHECK to portal_templates RLS policy.
-- SPEC-SEC-CROSS-TENANT-FOLLOWUP-001
--
-- Run as klai superuser AFTER alembic migration b2d3e4f5a6c7 completes:
--   docker exec klai-core-postgres-1 psql -U klai -d klai -f /tmp/post_deploy.sql
--
-- Background:
-- Migration 34d8f876ffbf shipped USING only, no explicit WITH CHECK.
-- PostgreSQL reuses USING as implicit WITH CHECK on FOR ALL policies.
-- When app.cross_org_admin=true, _rls_current_org_id() returns NULL,
-- so the implicit check resolves to TRUE for any org_id — a cross-org
-- admin session could write rows for any tenant. This adds a strict
-- explicit WITH CHECK that always requires a bound tenant context.
--
-- Cat-D policy shape after fix:
--   USING:      _rls_current_org_id() IS NULL OR org_id = _rls_current_org_id()
--   WITH CHECK: org_id = _rls_current_org_id()
--
-- Reference: process-rules.md alembic-cannot-drop-non-portal_api-tables (HIGH)

BEGIN;

DROP POLICY IF EXISTS tenant_isolation ON portal_templates;

CREATE POLICY tenant_isolation ON portal_templates
    FOR ALL
    USING      (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

COMMIT;
