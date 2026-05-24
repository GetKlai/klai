-- Post-deploy for 5b7c9d1e2f3a_add_widget_public_share_enabled.py.
--
-- Run as klai superuser after alembic upgrade head. Alembic runs as
-- portal_api, but public.widgets is owned by klai and FORCE RLS is enabled.
-- Both facts make the ALTER TABLE + cross-tenant backfill unsafe inside the
-- normal portal_api migration connection.

BEGIN;

ALTER TABLE public.widgets
    ADD COLUMN IF NOT EXISTS public_share_enabled boolean DEFAULT false NOT NULL;

SET LOCAL app.cross_org_admin = 'true';

UPDATE public.widgets
SET public_share_enabled = COALESCE((widget_config ->> 'public_share_enabled')::boolean, false)
WHERE widget_config ? 'public_share_enabled';

UPDATE public.widgets
SET widget_config = widget_config - 'public_share_enabled'
WHERE widget_config ? 'public_share_enabled';

ALTER TABLE public.widgets OWNER TO klai;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.widgets TO portal_api;

COMMIT;

SELECT 'post_deploy_5b7c9d1e2f3a_widget_public_share_enabled applied' AS status;
