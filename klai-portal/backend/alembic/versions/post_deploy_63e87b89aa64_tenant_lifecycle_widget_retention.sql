-- Widen the tenant_lifecycle_events event_type CHECK for the per-tenant widget
-- retention override (SPEC-KNOWLEDGE-ACTIVITY-001 follow-up, 15 Sep 2026).
--
-- Run as the 'klai' superuser. Same shape as
-- post_deploy_c0d5e2a7b9f3_tenant_lifecycle_platform_features.sql: the Python
-- validator in app/services/audit/tenant_lifecycle.py and this CHECK must list
-- the same values, otherwise every PATCH of the retention window fails before
-- its commit.

BEGIN;
ALTER TABLE tenant_lifecycle_events
    DROP CONSTRAINT IF EXISTS ck_tenant_lifecycle_events_event_type;
ALTER TABLE tenant_lifecycle_events
    ADD CONSTRAINT ck_tenant_lifecycle_events_event_type CHECK (
        event_type = ANY (ARRAY[
            'provisioned'::text,
            'deprovisioned'::text,
            'failed_deprovisioning'::text,
            'platform_features_updated'::text,
            'widget_retention_updated'::text
        ])
    );
COMMIT;
