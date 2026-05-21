-- Post-deploy SQL for revision a4f72e913c8b (widget_conversations + widget_messages).
--
-- portal_api lacks REFERENCES privilege on `widgets` (owned by klai),
-- so CREATE TABLE with FK(widget_id) REFERENCES widgets(id) inside an
-- alembic migration fails with 42501. The migration's upgrade() is a
-- no-op; all DDL lives here and runs as klai superuser.
--
-- Apply with:
--   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB' \
--   < klai-portal/backend/alembic/versions/post_deploy_a4f72e913c8b_widget_conversations_rls.sql"
--
-- Idempotent: every CREATE/ALTER uses IF NOT EXISTS so re-runs are safe.

BEGIN;

-- widget_conversations -------------------------------------------------
CREATE TABLE IF NOT EXISTS widget_conversations (
    id BIGSERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    widget_id UUID NOT NULL REFERENCES widgets(id) ON DELETE CASCADE,
    session_key TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    message_count INTEGER NOT NULL DEFAULT 0,
    ip_hash VARCHAR(64),
    user_agent_hash VARCHAR(64),
    first_user_query TEXT,
    language_detected VARCHAR(8),
    CONSTRAINT uq_widget_conversations_widget_session UNIQUE (widget_id, session_key)
);
CREATE INDEX IF NOT EXISTS ix_widget_conversations_widget_started
    ON widget_conversations (widget_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_widget_conversations_org_started
    ON widget_conversations (org_id, started_at DESC);

-- widget_messages ------------------------------------------------------
CREATE TABLE IF NOT EXISTS widget_messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES widget_conversations(id) ON DELETE CASCADE,
    org_id INTEGER NOT NULL,
    role VARCHAR(16) NOT NULL CHECK (role IN ('user','assistant')),
    content TEXT NOT NULL,
    sources JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sequence INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_widget_messages_conv_seq
    ON widget_messages (conversation_id, sequence);
CREATE INDEX IF NOT EXISTS ix_widget_messages_org_created
    ON widget_messages (org_id, created_at DESC);

-- Grant DML to portal_api so the runtime can SELECT / INSERT / UPDATE
-- via SQLAlchemy. RLS Cat-D enforces tenant isolation on top.
GRANT SELECT, INSERT, UPDATE, DELETE ON widget_conversations TO portal_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON widget_messages       TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE widget_conversations_id_seq TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE widget_messages_id_seq       TO portal_api;

-- RLS Cat-D strict policy (per portal-security.md): every access path
-- must SET app.current_org_id; missing GUC = raise (helper returns
-- NULL -> first OR clause skipped, fallback strict check fires).
ALTER TABLE widget_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE widget_conversations FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON widget_conversations;
CREATE POLICY tenant_isolation ON widget_conversations
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

ALTER TABLE widget_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE widget_messages FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON widget_messages;
CREATE POLICY tenant_isolation ON widget_messages
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

COMMIT;
