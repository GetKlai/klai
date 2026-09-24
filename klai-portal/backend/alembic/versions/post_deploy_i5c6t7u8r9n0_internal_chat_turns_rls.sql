-- One-chat-pipeline slice 5 — RLS for internal_chat_turns.
--
-- Run as the ``klai`` superuser AFTER ``alembic upgrade head`` completes
-- (``portal_api`` is not the table owner once it is handed to klai, and cannot
-- ENABLE RLS). Code first, SQL second: see
-- klai-infra/docs/runbooks/rls-upgrade.md for the full procedure.
--
-- Category D (strict): the only access path is the fire-and-forget writer
-- ``record_internal_turn`` in app/services/widget_audit.py, which opens
-- ``tenant_scoped_session(org_id)`` for the authenticated key's org before it
-- inserts, the same way ``widget_messages`` is written. No read happens
-- before tenant context exists, so the fail-loud helper applies and a row is
-- readable only inside its own org.
--
-- Idempotent: re-running is safe.

BEGIN;

ALTER TABLE public.internal_chat_turns OWNER TO klai;
ALTER TABLE public.internal_chat_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.internal_chat_turns FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON public.internal_chat_turns;

CREATE POLICY tenant_isolation ON public.internal_chat_turns
    USING (
        public._rls_current_org_id() IS NULL
        OR org_id = public._rls_current_org_id()
    )
    WITH CHECK (
        org_id = public._rls_current_org_id()
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON public.internal_chat_turns TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE public.internal_chat_turns_id_seq TO portal_api;

COMMIT;
