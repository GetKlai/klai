-- SPEC-TI-007 / C-1 post-deploy: RLS for knowledge.gitea_repo_to_org
-- Run as klai superuser AFTER alembic upgrade head.
-- Safe to re-run: IF NOT EXISTS / OR REPLACE guards.
--
-- Usage (on core-01):
--   ssh core-01 "docker exec -i klai-core-postgres-1 psql -U klai -d klai" < \
--       klai-knowledge-ingest/alembic/versions/post_deploy_0002_gitea_repo_to_org.sql

BEGIN;

-- Enable RLS (requires table owner = klai superuser, which it is per SPEC-TI-007)
ALTER TABLE knowledge.gitea_repo_to_org ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.gitea_repo_to_org FORCE ROW LEVEL SECURITY;

-- Cat-D policy: strict tenant isolation matching other knowledge-schema tables.
-- Uses the same _rls_current_org_id() helper as portal-api Cat-D tables.
-- CROSS-ORG bypass: knowledge-ingest webhook handler uses cross_org_session()
-- for the mapping SELECT so the NULLIS clause grants read access when GUC is
-- not set (service-to-service reads without tenant context).
DROP POLICY IF EXISTS tenant_isolation ON knowledge.gitea_repo_to_org;
CREATE POLICY tenant_isolation
    ON knowledge.gitea_repo_to_org
    USING (
        current_setting('app.current_org_id', true) IS NULL
        OR org_id = current_setting('app.current_org_id', true)
    );

-- Grant portal_api INSERT/UPDATE so the admin endpoint (or bootstrap script)
-- can populate mappings.
GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge.gitea_repo_to_org TO portal_api;

COMMIT;
