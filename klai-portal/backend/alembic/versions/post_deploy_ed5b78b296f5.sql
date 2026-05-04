-- Post-deploy DDL for SPEC-AUTH-009 migration ed5b78b296f5.
-- Run as `klai` superuser after `alembic upgrade ed5b78b296f5` completes.
-- The Alembic migration itself cannot run this statement because the
-- migration role (`portal_api`) is not the table owner of
-- `portal_org_allowed_domains` — DROP TABLE raises InsufficientPrivilegeError
-- and crashes the portal-api entrypoint into a restart loop.
--
-- Idempotent: safe to re-run.
--
-- Pattern mirrors post_deploy_f0a1b2c3d4e5.sql (SPEC-WIDGET-002) and the RLS
-- post-deploys.  See klai/projects/portal-backend.md "RLS + Alembic".

-- 1. Drop the SPEC-AUTH-006 allowed-domains table; replaced by
--    portal_orgs.primary_domain (added in this migration's upgrade()).
DROP TABLE IF EXISTS portal_org_allowed_domains CASCADE;
