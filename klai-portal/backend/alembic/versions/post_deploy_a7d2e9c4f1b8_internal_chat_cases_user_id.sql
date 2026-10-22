-- internal_chat_cases.user_id
--
-- Run as the 'klai' superuser (portal_api cannot ALTER the klai-owned table).
-- Apply after: alembic upgrade head stamps revision a7d2e9c4f1b8.
--
-- The Zitadel user id of the employee a stored case belongs to, so the
-- offboarding and user-delete paths can delete that employee's rows
-- (widget_audit.forget_internal_cases). Nullable in the schema, but rows
-- stored before this column have no employee, so offboarding could not find
-- them: they are deleted here, which costs those conversations at most one
-- turn of memory. Every write after this deploy fills the column. No grant
-- or policy changes: the Cat-D tenant_isolation policy covers the column.
--
-- Idempotent: re-running is safe.

BEGIN;

ALTER TABLE public.internal_chat_cases ADD COLUMN IF NOT EXISTS user_id VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_internal_chat_cases_org_user ON public.internal_chat_cases (org_id, user_id);
DELETE FROM public.internal_chat_cases WHERE user_id IS NULL;

COMMIT;
