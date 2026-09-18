-- Post-deploy for s2r3v4w5e6r7_add_support_case_reviews.py.
--
-- Run as the klai superuser AFTER `alembic upgrade head`. Alembic runs as
-- portal_api, but public.portal_support_cases is owned by klai with FORCE RLS
-- enabled, so the ALTER TABLE cannot run inside the portal_api migration.
--
-- Pure additive, metadata-only: a nullable JSONB column with no default and no
-- backfill. Existing rows read as NULL, which the app treats as {}. Idempotent
-- via ADD COLUMN IF NOT EXISTS; ownership and grants are unchanged (the column
-- inherits the table's existing klai owner + portal_api DML grant).

BEGIN;

ALTER TABLE public.portal_support_cases
    ADD COLUMN IF NOT EXISTS reviews jsonb;

COMMIT;

SELECT 'post_deploy_s2r3v4w5e6r7_support_case_reviews applied' AS status;
