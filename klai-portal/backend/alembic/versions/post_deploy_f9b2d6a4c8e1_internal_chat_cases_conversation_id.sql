-- internal_chat_cases keyed by LibreChat's conversation id
--
-- Run as the 'klai' superuser (portal_api cannot ALTER the klai-owned table).
-- Apply after: alembic upgrade head stamps revision f9b2d6a4c8e1.
--
-- One row per (org, employee, LibreChat conversation id), replacing the key
-- built from digests of the employee's messages. Rows keyed by digests cannot
-- be mapped to a conversation id, so they are deleted: that costs each open
-- conversation the memory of one turn, and the table holds at most 30 days.
-- deploy-portal-api.sh applies every post-deploy file on every deploy, in name
-- order, so this file runs after the user_id file (a7d2e9c4f1b8) and the RLS
-- file (f3a9c6e2b4d1), and every statement is a no-op once it has run: the
-- DELETE only ever finds rows from before the column existed. No grant or
-- policy changes: the Cat-D tenant_isolation policy covers the new column.
--
-- Idempotent: re-running is safe.

BEGIN;

ALTER TABLE public.internal_chat_cases ADD COLUMN IF NOT EXISTS conversation_id TEXT;
DELETE FROM public.internal_chat_cases WHERE conversation_id IS NULL OR user_id IS NULL;
ALTER TABLE public.internal_chat_cases DROP CONSTRAINT IF EXISTS uq_internal_chat_cases_org_key;
ALTER TABLE public.internal_chat_cases DROP COLUMN IF EXISTS conversation_key;
ALTER TABLE public.internal_chat_cases DROP COLUMN IF EXISTS turn_digest;
ALTER TABLE public.internal_chat_cases ALTER COLUMN conversation_id SET NOT NULL;
ALTER TABLE public.internal_chat_cases ALTER COLUMN user_id SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_internal_chat_cases_org_user_conversation
    ON public.internal_chat_cases (org_id, user_id, conversation_id);

COMMIT;
