-- Post-deploy SQL for SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
-- Revision: a1c2d3e4f5b6
--
-- Run as klai superuser AFTER `alembic upgrade head` completes.
-- Applies the 3-branch data migration for existing widget rows:
--
--   Branch 1: widgets with a non-empty allowed_origins list → keep as-is.
--             allow_any_origin stays false (default); no changes needed.
--
--   Branch 2: widgets with empty/missing allowed_origins AND public_share_enabled=true
--             → set allow_any_origin=true. These widgets were intentionally
--             open to the world before REQ-2 (origin_allowed returned True on
--             empty list). Preserving that intent is the safe default here.
--
--   Branch 3: widgets with empty/missing allowed_origins AND public_share_enabled=false
--             → set allowed_origins=["https://<tenant_slug>.getklai.com"] in
--             widget_config JSONB. Locks to the tenant's own portal domain so
--             the widget does not silently deny all traffic after the origin
--             gate default-deny change.
--
-- The audit events in the INSERT below use the portal_audit_log table (Cat-C
-- RLS: INSERT permissive, so no GUC needed).

BEGIN;

-- Branch 2: empty origins + public_share_enabled → set allow_any_origin.
UPDATE widgets
SET allow_any_origin = true
WHERE jsonb_array_length(COALESCE(widget_config->'allowed_origins', '[]'::jsonb)) = 0
  AND public_share_enabled = true;

-- Emit audit events for Branch 2 migrations.
INSERT INTO portal_audit_log (org_id, event_type, actor_type, properties, created_at)
SELECT
    org_id,
    'widget.allow_any_origin_migrated',
    'system',
    jsonb_build_object(
        'widget_id', id,
        'reason', 'public_share_enabled',
        'migration_revision', 'a1c2d3e4f5b6'
    ),
    NOW()
FROM widgets
WHERE allow_any_origin = true
  AND jsonb_array_length(COALESCE(widget_config->'allowed_origins', '[]'::jsonb)) = 0;

-- Branch 3: empty origins + public_share_enabled=false → fill tenant subdomain.
-- Joins to portal_orgs to get the slug for the URL.
UPDATE widgets w
SET widget_config = jsonb_set(
    COALESCE(w.widget_config, '{}'::jsonb),
    '{allowed_origins}',
    jsonb_build_array('https://' || o.slug || '.getklai.com')
)
FROM portal_orgs o
WHERE w.org_id = o.id
  AND jsonb_array_length(COALESCE(w.widget_config->'allowed_origins', '[]'::jsonb)) = 0
  AND w.public_share_enabled = false
  AND w.allow_any_origin = false;

-- Emit audit events for Branch 3 migrations.
INSERT INTO portal_audit_log (org_id, event_type, actor_type, properties, created_at)
SELECT
    w.id,
    'widget.allow_any_origin_migrated',
    'system',
    jsonb_build_object(
        'widget_id', w.id,
        'reason', 'tenant_subdomain_default',
        'tenant_slug', o.slug,
        'migration_revision', 'a1c2d3e4f5b6'
    ),
    NOW()
FROM widgets w
JOIN portal_orgs o ON o.id = w.org_id
WHERE jsonb_array_length(COALESCE(w.widget_config->'allowed_origins', '[]'::jsonb)) = 1
  AND w.widget_config->'allowed_origins'->0 LIKE 'https://%.getklai.com'
  AND w.allow_any_origin = false
  AND w.public_share_enabled = false;

COMMIT;
