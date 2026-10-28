-- SPEC-SLACK-001 §6.2 — RLS + partner-key FK for portal_slack_installations.
--
-- Run as the ``klai`` superuser AFTER ``alembic upgrade head`` completes
-- (``portal_api`` is not the table owner once it is handed to klai, cannot
-- ENABLE RLS, and has no REFERENCES privilege on the klai-owned
-- partner_api_keys). Code first, SQL second: see
-- klai-infra/docs/runbooks/rls-upgrade.md for the full procedure.
--
-- Category D (strict): every access path sets tenant context first. The admin
-- routes run under get_caller's set_tenant, POST /internal/slack/link calls
-- set_tenant for the org of the verified key before it touches this table,
-- tenant deprovisioning deletes under its own set_tenant, and
-- DELETE /internal/slack/link/{team_id} (which only knows the Slack team)
-- uses one cross_org_scope UPDATE. The fail-loud helper therefore applies.
--
-- Idempotent: re-running is safe.

BEGIN;

ALTER TABLE public.portal_slack_installations OWNER TO klai;
ALTER TABLE public.portal_slack_installations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.portal_slack_installations FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON public.portal_slack_installations;

CREATE POLICY tenant_isolation ON public.portal_slack_installations
    USING (
        public._rls_current_org_id() IS NULL
        OR org_id = public._rls_current_org_id()
    )
    WITH CHECK (
        org_id = public._rls_current_org_id()
    );

GRANT SELECT, INSERT, UPDATE, DELETE ON public.portal_slack_installations TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE public.portal_slack_installations_id_seq TO portal_api;

-- Deleting the Slack key (admin disconnect, rotation by delete + recreate)
-- removes the installation row with it.
ALTER TABLE public.portal_slack_installations
    DROP CONSTRAINT IF EXISTS fk_portal_slack_installations_partner_api_key;
ALTER TABLE public.portal_slack_installations
    ADD CONSTRAINT fk_portal_slack_installations_partner_api_key
    FOREIGN KEY (partner_api_key_id)
    REFERENCES public.partner_api_keys (id)
    ON DELETE CASCADE;

COMMIT;
