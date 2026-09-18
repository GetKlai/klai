-- SPEC-RAG-SUPPORT-GAP — RLS + cross-table FK for portal_support_cases.
--
-- Run as the ``klai`` superuser AFTER ``alembic upgrade head`` completes
-- (``portal_api`` is not the table owner, cannot ENABLE RLS, and has no
-- REFERENCES privilege on the new klai-owned table). See
-- klai-portal/backend/docs/runbooks/rls-upgrade.md for the full procedure.
--
-- Category D (strict): every access path sets app.current_org_id via
-- set_tenant before touching portal_support_cases. The helper raises 42501
-- when the GUC is missing — fail-loud, mirrors portal_retrieval_gaps.

BEGIN;

ALTER TABLE public.portal_support_cases OWNER TO klai;
ALTER TABLE public.portal_support_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.portal_support_cases FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON public.portal_support_cases;

CREATE POLICY tenant_isolation ON public.portal_support_cases
    USING (
        public._rls_current_org_id() IS NULL
        OR org_id = public._rls_current_org_id()
    )
    WITH CHECK (
        org_id = public._rls_current_org_id()
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON public.portal_support_cases TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE public.portal_support_cases_id_seq TO portal_api;

-- Deleting a case (connector delete, reconciliation, retention, privacy purge)
-- removes its derived inbox findings. The FK is added here because portal_api
-- lacks REFERENCES on the klai-owned portal_support_cases — same split as the
-- gaps->widget_conversations and gaps->portal_users FKs.
ALTER TABLE public.portal_retrieval_gaps
    DROP CONSTRAINT IF EXISTS fk_retrieval_gaps_support_case;
ALTER TABLE public.portal_retrieval_gaps
    ADD CONSTRAINT fk_retrieval_gaps_support_case
    FOREIGN KEY (support_case_id)
    REFERENCES public.portal_support_cases (id)
    ON DELETE CASCADE;

-- Deleting the owning connector removes its cases (and, via the FK above, their
-- findings). Audio cases keep connector_id NULL and are unaffected here.
ALTER TABLE public.portal_support_cases
    DROP CONSTRAINT IF EXISTS fk_support_cases_connector;
ALTER TABLE public.portal_support_cases
    ADD CONSTRAINT fk_support_cases_connector
    FOREIGN KEY (connector_id)
    REFERENCES public.portal_connectors (id)
    ON DELETE CASCADE;

-- Deleting the comparison-scope KB removes its cases (including audio cases,
-- which have no connector) and their findings. Composite same-org FK against
-- the existing UNIQUE(org_id, slug) so it cannot cross a tenant boundary.
ALTER TABLE public.portal_support_cases
    DROP CONSTRAINT IF EXISTS fk_support_cases_kb;
ALTER TABLE public.portal_support_cases
    ADD CONSTRAINT fk_support_cases_kb
    FOREIGN KEY (org_id, kb_slug)
    REFERENCES public.portal_knowledge_bases (org_id, slug)
    ON DELETE CASCADE;

COMMIT;
