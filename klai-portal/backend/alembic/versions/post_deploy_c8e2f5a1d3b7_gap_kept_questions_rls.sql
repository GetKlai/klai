-- Kept gap questions (owner decision 2026-11-11) — RLS for portal_gap_kept_questions.
--
-- Run as the ``klai`` superuser AFTER ``alembic upgrade head`` completes
-- (``portal_api`` is not the table owner once it is handed to klai and cannot
-- ENABLE RLS). Code first, SQL second: see klai-infra/docs/runbooks/rls-upgrade.md.
--
-- Category D (strict), like portal_gap_proposals and portal_retrieval_gaps.
-- Every access path is either tenant-scoped or the documented cross-org purge:
--   * the widget retention sweep inserts in ``tenant_scoped_session(org_id)``
--     (app/services/widget_messages_retention.py, _purge_org_chunk);
--   * the gap list reads, a decision and a test mark delete, on the request
--     session after ``Depends(get_caller)`` (app/api/app_gap_clusters.py,
--     app/api/app_activity.py);
--   * leaving telemetry full deletes in ``tenant_scoped_session(org_id)``
--     (app/services/telemetry_level.py);
--   * the daily purge deletes in ``cross_org_session()``
--     (app/services/telemetry_purge.py), where USING lets the DELETE through
--     and WITH CHECK is not evaluated.
--
-- Idempotent: re-running is safe.

BEGIN;

ALTER TABLE public.portal_gap_kept_questions OWNER TO klai;
ALTER TABLE public.portal_gap_kept_questions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.portal_gap_kept_questions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON public.portal_gap_kept_questions;

CREATE POLICY tenant_isolation ON public.portal_gap_kept_questions
    USING (
        public._rls_current_org_id() IS NULL
        OR org_id = public._rls_current_org_id()
    )
    WITH CHECK (
        org_id = public._rls_current_org_id()
    );

GRANT SELECT, INSERT, DELETE ON public.portal_gap_kept_questions TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE public.portal_gap_kept_questions_id_seq TO portal_api;

COMMIT;
