-- Klai dev seed data — populates a local database with demo content.
-- Usage: make seed
-- Idempotent: safe to run multiple times (ON CONFLICT DO NOTHING).

-- Dev organization (matches app/core/dev_seed.py auto-seed)
INSERT INTO portal_orgs (zitadel_org_id, name, slug, plan, billing_status, provisioning_status)
VALUES ('dev-org-1', 'Dev Organization', 'dev', 'professional', 'active', 'complete')
ON CONFLICT (zitadel_org_id) DO NOTHING;

-- Dev user (matches AUTH_DEV_USER_ID=dev-user-1 default)
INSERT INTO portal_users (zitadel_user_id, org_id, role, display_name, email, status)
SELECT 'dev-user-1', id, 'admin', 'Dev User', 'dev@klai.local', 'active'
FROM portal_orgs WHERE zitadel_org_id = 'dev-org-1'
ON CONFLICT (zitadel_user_id, org_id) DO NOTHING;

-- Second test user (for multi-user testing)
INSERT INTO portal_users (zitadel_user_id, org_id, role, display_name, email, status)
SELECT 'dev-user-2', id, 'member', 'Test Member', 'member@klai.local', 'active'
FROM portal_orgs WHERE zitadel_org_id = 'dev-org-1'
ON CONFLICT (zitadel_user_id, org_id) DO NOTHING;
