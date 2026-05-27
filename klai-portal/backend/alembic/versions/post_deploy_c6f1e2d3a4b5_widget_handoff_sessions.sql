-- post_deploy_c6f1e2d3a4b5_widget_handoff_sessions.sql
-- Creates persistence for realtime widget -> HubSpot human handoff.

BEGIN;

CREATE TABLE IF NOT EXISTS widget_handoff_sessions (
    id BIGSERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    widget_id UUID NOT NULL REFERENCES widgets(id) ON DELETE NO ACTION,
    conversation_id BIGINT NOT NULL REFERENCES widget_conversations(id) ON DELETE CASCADE,
    provider VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'starting',
    integration_thread_id TEXT NOT NULL,
    hubspot_conversations_thread_id TEXT,
    hubspot_channel_account_id TEXT,
    hubspot_contact_id TEXT,
    visitor_name TEXT,
    visitor_email TEXT,
    summary TEXT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    activated_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_widget_handoff_sessions_provider
        CHECK (provider IN ('hubspot')),
    CONSTRAINT ck_widget_handoff_sessions_status
        CHECK (status IN ('starting', 'active', 'closed', 'failed'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_widget_handoff_sessions_provider_thread
    ON widget_handoff_sessions (provider, integration_thread_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_widget_handoff_sessions_provider_conversation
    ON widget_handoff_sessions (provider, conversation_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_widget_handoff_sessions_provider_hubspot_thread
    ON widget_handoff_sessions (provider, hubspot_conversations_thread_id)
    WHERE hubspot_conversations_thread_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_widget_handoff_sessions_org_created
    ON widget_handoff_sessions (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_widget_handoff_sessions_widget_status
    ON widget_handoff_sessions (widget_id, status);

CREATE TABLE IF NOT EXISTS widget_handoff_messages (
    id BIGSERIAL PRIMARY KEY,
    handoff_session_id BIGINT NOT NULL REFERENCES widget_handoff_sessions(id) ON DELETE CASCADE,
    org_id INTEGER NOT NULL,
    direction VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    hubspot_message_id TEXT,
    integration_idempotency_id TEXT,
    visible_to_visitor BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_widget_handoff_messages_direction
        CHECK (direction IN ('visitor', 'agent', 'system'))
);

CREATE INDEX IF NOT EXISTS ix_widget_handoff_messages_session_created
    ON widget_handoff_messages (handoff_session_id, created_at);
CREATE INDEX IF NOT EXISTS ix_widget_handoff_messages_org_created
    ON widget_handoff_messages (org_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_widget_handoff_messages_hubspot_message
    ON widget_handoff_messages (hubspot_message_id)
    WHERE hubspot_message_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_widget_handoff_messages_idempotency
    ON widget_handoff_messages (integration_idempotency_id)
    WHERE integration_idempotency_id IS NOT NULL;

GRANT SELECT, INSERT, UPDATE, DELETE ON widget_handoff_sessions TO portal_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON widget_handoff_messages TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE widget_handoff_sessions_id_seq TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE widget_handoff_messages_id_seq TO portal_api;

ALTER TABLE widget_handoff_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE widget_handoff_sessions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON widget_handoff_sessions;
CREATE POLICY tenant_isolation ON widget_handoff_sessions
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

ALTER TABLE widget_handoff_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE widget_handoff_messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON widget_handoff_messages;
CREATE POLICY tenant_isolation ON widget_handoff_messages
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

COMMIT;
