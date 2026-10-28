-- Knowledge-gap-to-article phase 1 — RLS and the decided_by FK for portal_gap_proposals.
--
-- Run as the ``klai`` superuser AFTER ``alembic upgrade head`` completes
-- (``portal_api`` is not the table owner once it is handed to klai, cannot
-- ENABLE RLS, and has no REFERENCES privilege on portal_users). Code first,
-- SQL second: see klai-infra/docs/runbooks/rls-upgrade.md.
--
-- Category D (strict), like portal_retrieval_gaps: every access path is
-- request-scoped through ``Depends(get_caller)`` (app/api/app_gap_clusters.py)
-- or a ``tenant_scoped_session`` (scripts/knowledge_gap_report.py), so the
-- fail-loud helper applies.
--
-- decided_by: ON DELETE SET NULL, not CASCADE: a departed colleague must not
-- take the decision with them; the cluster stays decided, only the attribution
-- is cleared.
--
-- Idempotent: re-running is safe.

BEGIN;

ALTER TABLE public.portal_gap_proposals OWNER TO klai;
ALTER TABLE public.portal_gap_proposals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.portal_gap_proposals FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON public.portal_gap_proposals;

CREATE POLICY tenant_isolation ON public.portal_gap_proposals
    USING (
        public._rls_current_org_id() IS NULL
        OR org_id = public._rls_current_org_id()
    )
    WITH CHECK (
        org_id = public._rls_current_org_id()
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON public.portal_gap_proposals TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE public.portal_gap_proposals_id_seq TO portal_api;

ALTER TABLE public.portal_gap_proposals
    DROP CONSTRAINT IF EXISTS fk_gap_proposals_decided_by;
ALTER TABLE public.portal_gap_proposals
    ADD CONSTRAINT fk_gap_proposals_decided_by
    FOREIGN KEY (decided_by)
    REFERENCES public.portal_users (id)
    ON DELETE SET NULL;

COMMIT;
