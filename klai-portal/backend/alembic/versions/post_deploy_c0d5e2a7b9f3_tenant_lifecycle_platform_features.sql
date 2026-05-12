-- post_deploy_c0d5e2a7b9f3_tenant_lifecycle_platform_features.sql
-- SPEC-PORTAL-EXTENSIONS-UNIFY-001 Phase 4 hotfix.
--
-- Extends ck_tenant_lifecycle_events_event_type to allow the
-- 'platform_features_updated' event type emitted by the platform-unlock
-- update path (admin/platform_unlocks.py + admin/extensions.py).
--
-- Run as klai superuser (NOT portal_api) because tenant_lifecycle_events
-- is owned by klai. The pattern matches the RLS post-deploy SQL files in
-- this directory.
--
-- Idempotent: DROP IF EXISTS + named ADD CONSTRAINT means repeated runs
-- (e.g. retried deploys) leave the constraint in its target state.
--
-- Live prod applied 2026-05-12 17:00 CEST via psql; this file captures
-- the same change for reproducibility in any rebuilt environment.

BEGIN;

ALTER TABLE tenant_lifecycle_events
    DROP CONSTRAINT IF EXISTS ck_tenant_lifecycle_events_event_type;

ALTER TABLE tenant_lifecycle_events
    ADD CONSTRAINT ck_tenant_lifecycle_events_event_type CHECK (
        event_type = ANY (ARRAY[
            'provisioned'::text,
            'deprovisioned'::text,
            'failed_deprovisioning'::text,
            'platform_features_updated'::text
        ])
    );

COMMIT;
