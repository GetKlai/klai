-- Post-deploy SQL for revision 4d7e1a9c2b63 (durable support reanalysis request).
--
-- Applied as the klai superuser by deploy/scripts/deploy-portal-api.sh before
-- the new portal-api container starts. Manual apply:
--   ssh core-01 "docker exec -i klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB'" \
--   < klai-portal/backend/alembic/versions/post_deploy_4d7e1a9c2b63_support_reanalysis_request.sql
--
-- Idempotent: the deploy script re-applies every post-deploy file.

BEGIN;

-- Set by a connector sync that changed knowledge, restamped by each later one
-- (restarting the debounce), cleared by the support reanalysis loop after the
-- run it triggered. NULL means no reanalysis is pending.
ALTER TABLE portal_orgs ADD COLUMN IF NOT EXISTS support_reanalysis_requested_at TIMESTAMPTZ;

COMMIT;
