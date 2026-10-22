-- RLS for internal_chat_cases (the case an internal conversation carries to its next turn).
--
-- Run as the ``klai`` superuser AFTER ``alembic upgrade head`` completes
-- (``portal_api`` is not the table owner once it is handed to klai, and cannot
-- ENABLE RLS). Code first, SQL second: see
-- klai-infra/docs/runbooks/rls-upgrade.md for the full procedure.
--
-- Category D (strict): the only access paths are ``last_internal_case`` and
-- ``keep_internal_case`` in app/services/widget_audit.py, which open
-- ``tenant_scoped_session(org_id)`` for the authenticated key's org before
-- they read, upsert or prune, the same way ``internal_chat_turns`` is
-- written. No access happens before tenant context exists, so the fail-loud
-- helper applies and a row is readable only inside its own org.
--
-- Idempotent: re-running is safe.

BEGIN;

ALTER TABLE public.internal_chat_cases OWNER TO klai;
ALTER TABLE public.internal_chat_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.internal_chat_cases FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON public.internal_chat_cases;

CREATE POLICY tenant_isolation ON public.internal_chat_cases
    USING (
        public._rls_current_org_id() IS NULL
        OR org_id = public._rls_current_org_id()
    )
    WITH CHECK (
        org_id = public._rls_current_org_id()
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON public.internal_chat_cases TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE public.internal_chat_cases_id_seq TO portal_api;

COMMIT;
