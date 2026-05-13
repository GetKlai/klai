-- SPEC-KB-FILE-UPLOAD-001 — RLS policies for kb_uploads.
--
-- Run as ``klai`` superuser AFTER alembic upgrade head completes
-- (``portal_api`` role is not the table owner and cannot ENABLE RLS).
-- See klai-portal/backend/docs/runbooks/rls-upgrade.md for the full
-- procedure.
--
-- Category D (strict): every access path sets app.current_org_id via
-- the `_get_caller_org` dependency before touching kb_uploads. The
-- helper raises 42501 when the GUC is missing — fail-loud is
-- intentional, mirrors portal_knowledge_bases.

BEGIN;

ALTER TABLE public.kb_uploads OWNER TO klai;
ALTER TABLE public.kb_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kb_uploads FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON public.kb_uploads;

CREATE POLICY tenant_isolation ON public.kb_uploads
    USING (
        public._rls_current_org_id() IS NULL
        OR org_id = public._rls_current_org_id()
    )
    WITH CHECK (
        org_id = public._rls_current_org_id()
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON public.kb_uploads TO portal_api;

COMMIT;
