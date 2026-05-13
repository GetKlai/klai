-- Post-deploy data backfill for SPEC-PORTAL-RBAC-REFACTOR-001 Phase 5
-- (migration h1i2j3k4l5m6 added the platform_unlocked_features column).
--
-- Run as the `klai` superuser AFTER `alembic upgrade h1i2j3k4l5m6`
-- completes. Safe to re-run — the WHERE clauses make every UPDATE
-- idempotent.
--
-- Why post-deploy and not part of the alembic migration itself:
-- This is a tenant-specific data fix, not a schema change. Encoding
-- "tenant slug = 'getklai' starts with widgets+custom_mcps unlocked"
-- directly inside an alembic upgrade body is not how klai handles
-- tenant-config seeding. Other tenant-config seeding follows the
-- same `alembic/versions/post_deploy_*.sql` convention used by
-- SPEC-SEC-PORTAL-RLS-001 (see post_deploy_2f7d1eae1198.sql header).
--
-- Why this fix exists:
-- Phase 5 added the gate `assert_platform_unlocked(org, "<feature>")` to
-- partner_dependencies / admin_widgets / mcp_servers without
-- grandfathering. The SPEC body (lines 169-201) said "geen actieve
-- gebruikers met partner_api_keys; custom-MCP-feature kan nog niet door
-- tenants gebruikt worden — dus niets om te grandfatheren". That is true
-- for tenant orgs (Voys at the time of writing has neither widgets nor
-- custom MCPs configured). It was NOT true for the platform org
-- ('getklai'):
--   - 'getklai' has 2 widgets in `widgets`
--   - 'getklai' has the 'twenty-crm' custom MCP enabled
--     (`portal_orgs.mcp_servers->>twenty-crm.enabled = true`)
-- After Phase 5 deploy these endpoints started returning HTTP 403
-- `feature_not_unlocked` for getklai-admin operations on widgets or
-- on toggling the existing custom MCP. The user-visible incident was
-- detected and fixed via a manual UPDATE on prod the same day. This
-- migration captures that fix in source so:
--   1. Fresh staging / dev / disaster-recovery DBs reach the same end
--      state without operator intervention.
--   2. The unlock state for the platform org is documented in code,
--      not in shell history.
--   3. Future post-incident audits can locate the fix via grep.
--
-- The audit-trail (tenant_lifecycle_events with event_type =
-- 'platform_features_updated') is intentionally NOT emitted by this
-- migration — that audit type is for runtime PATCH /platform-unlocks
-- changes by a platform-admin actor. A migration is not an actor.
-- Forensic reconstruction of "when did getklai get these unlocks" is
-- via this migration file in git history + the post-deploy SQL apply
-- log on core-01.
--
-- The hardcoded 'getklai' slug is correct: it is the platform org
-- (single, never multi-instance) per `settings.platform_org_slug`. Other
-- tenants legitimately start with `'{}'` — they do not have widgets or
-- custom MCPs and unlocking either feature for a tenant must go through
-- the audited `PATCH /api/admin/orgs/{slug}/platform-unlocks` route.

BEGIN;

UPDATE portal_orgs
SET platform_unlocked_features = ARRAY['widgets', 'custom_mcps']::text[]
WHERE slug = 'getklai'
  AND NOT (platform_unlocked_features @> ARRAY['widgets', 'custom_mcps']::text[]);

COMMIT;
