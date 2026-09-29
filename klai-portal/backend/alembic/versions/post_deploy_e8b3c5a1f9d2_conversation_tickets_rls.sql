-- Post-deploy SQL for revision e8b3c5a1f9d2 (widget_ticket_settings +
-- conversation_tickets). SPEC-KNOWLEDGE-ESCALATION-001 §4.1.
--
-- portal_api lacks REFERENCES privilege on `widgets`, `widget_conversations`,
-- `portal_orgs` and `portal_users` (all owned by klai), so CREATE TABLE with
-- those foreign keys inside an alembic migration fails with 42501. The
-- migration's upgrade() is a no-op; all DDL lives here and runs as klai
-- superuser.
--
-- Apply with:
--   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB' \
--   < klai-portal/backend/alembic/versions/post_deploy_e8b3c5a1f9d2_conversation_tickets_rls.sql"
--
-- Idempotent: every CREATE uses IF NOT EXISTS and every policy is dropped
-- before it is created, so re-runs are safe.

BEGIN;

CREATE TABLE IF NOT EXISTS widget_ticket_settings (
    widget_id UUID PRIMARY KEY REFERENCES widgets(id) ON DELETE CASCADE,
    org_id INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    -- AES-256-GCM ciphertext from portal_secrets.encrypt; the plaintext key
    -- is never stored and never returned by any route.
    service_key_encrypted BYTEA NOT NULL,
    -- Entered by the admin: GET /account-info/v3/details would return it but
    -- accepts only the `oauth` scope, which tenant service keys lack.
    hubspot_portal_id BIGINT NOT NULL CHECK (hubspot_portal_id > 0),
    -- [{"key","label","pipeline_id","stage_id"}], 1-5 items with a unique
    -- slug key; validated by the admin route before it is written.
    targets JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by_user_id INTEGER REFERENCES portal_users(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS conversation_tickets (
    id BIGSERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    -- SET NULL, not CASCADE: the record that a conversation went to HubSpot
    -- must outlive the widget_messages_retention_days purge (default 7 days)
    -- of the conversation itself, same reasoning as answer_reviews.
    conversation_id BIGINT REFERENCES widget_conversations(id) ON DELETE SET NULL,
    target_key TEXT NOT NULL,
    -- Snapshot of the target label at creation time.
    target_label TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'created', 'failed')),
    hubspot_ticket_id TEXT,
    hubspot_contact_id TEXT,
    -- 'not_found': no contact matched the visitor's email and none was
    -- created (the key has no contacts.write scope), so the ticket has no
    -- contact association.
    contact_status TEXT CHECK (contact_status IN ('existing', 'not_found')),
    ticket_url TEXT,
    -- Short human-readable reason of the last failed attempt.
    error TEXT,
    created_by_user_id INTEGER REFERENCES portal_users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One ticket per conversation and target; the create route's ON CONFLICT
-- relies on this index to stop a double click from making two tickets.
-- Partial, because purged rows (conversation_id NULL) must not collide.
CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_tickets_conversation_target
    ON conversation_tickets (conversation_id, target_key)
    WHERE conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_conversation_tickets_org_created
    ON conversation_tickets (org_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON widget_ticket_settings TO portal_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON conversation_tickets TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE conversation_tickets_id_seq TO portal_api;

-- RLS Cat-D strict policy (per portal-security.md), mirrors answer_reviews:
-- every access path must SET app.current_org_id.
ALTER TABLE widget_ticket_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE widget_ticket_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON widget_ticket_settings;
CREATE POLICY tenant_isolation ON widget_ticket_settings
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

ALTER TABLE conversation_tickets ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_tickets FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON conversation_tickets;
CREATE POLICY tenant_isolation ON conversation_tickets
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

COMMIT;
