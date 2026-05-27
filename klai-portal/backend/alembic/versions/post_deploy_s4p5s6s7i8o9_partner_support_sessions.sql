-- Post-deploy SQL for revision s4p5s6s7i8o9 (partner support sessions).
--
-- Apply as table owner / superuser after Alembic reaches this revision.
-- Idempotent: safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS partner_support_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    partner_api_key_id UUID NOT NULL REFERENCES partner_api_keys(id) ON DELETE CASCADE,
    integration_type VARCHAR(64) NOT NULL,
    hubspot_portal_id VARCHAR(64) NOT NULL,
    hubspot_ticket_id VARCHAR(64) NOT NULL,
    hubspot_user_id_hash VARCHAR(64) NOT NULL,
    contact_id VARCHAR(64),
    subject_snapshot TEXT,
    content_snapshot TEXT,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    session_metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at TIMESTAMPTZ,
    message_count INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT ck_partner_support_sessions_integration_type
        CHECK (integration_type IN ('hubspot_email_support')),
    CONSTRAINT ck_partner_support_sessions_status
        CHECK (status IN ('active','archived')),
    CONSTRAINT uq_partner_support_session_scope UNIQUE (
        org_id,
        partner_api_key_id,
        integration_type,
        hubspot_portal_id,
        hubspot_ticket_id,
        hubspot_user_id_hash
    )
);

CREATE INDEX IF NOT EXISTS ix_partner_support_sessions_org_updated
    ON partner_support_sessions (org_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_partner_support_sessions_ticket
    ON partner_support_sessions (org_id, hubspot_portal_id, hubspot_ticket_id);

CREATE TABLE IF NOT EXISTS partner_support_messages (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES partner_support_sessions(id) ON DELETE CASCADE,
    org_id INTEGER NOT NULL,
    role VARCHAR(16) NOT NULL CHECK (role IN ('agent','assistant','system')),
    content TEXT NOT NULL,
    draft_body TEXT,
    sources JSONB,
    model_alias VARCHAR(64),
    completion_id VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sequence INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_partner_support_messages_session_seq
    ON partner_support_messages (session_id, sequence);
CREATE INDEX IF NOT EXISTS ix_partner_support_messages_org_created
    ON partner_support_messages (org_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON partner_support_sessions TO portal_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON partner_support_messages TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE partner_support_messages_id_seq TO portal_api;

ALTER TABLE partner_support_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_support_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON partner_support_sessions;
CREATE POLICY tenant_isolation ON partner_support_sessions
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

ALTER TABLE partner_support_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE partner_support_messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON partner_support_messages;
CREATE POLICY tenant_isolation ON partner_support_messages
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

COMMIT;
