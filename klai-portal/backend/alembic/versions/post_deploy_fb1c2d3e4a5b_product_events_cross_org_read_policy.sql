-- SPEC-KLAI-FEEDBACK-001
-- Allow Platform admin feedback views to read first-party assistant
-- submissions from product_events across tenants.
--
-- product_events intentionally kept the old inline tenant read policy because
-- fire-and-forget writes can run without app.current_org_id. Platform reads,
-- however, use cross_org_session(), which signals an explicit staff-only
-- bypass through app.cross_org_admin=true. The old policy ignored that signal,
-- so /api/admin/platform/feedback-submissions always returned [] even after
-- successful submissions.

DROP POLICY IF EXISTS tenant_read ON product_events;

CREATE POLICY tenant_read ON product_events
    FOR SELECT TO portal_api
    USING (
        current_setting('app.cross_org_admin', true) = 'true'
        OR org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
    );
