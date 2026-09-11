-- Post-deploy SQL for revision b7e4f1a9c3d2 (conversation_quality_judgments).
-- SPEC-CHAT-QUALITY-LOOP-001 REQ-1.
--
-- portal_api lacks REFERENCES privilege on `widget_conversations` (owned by
-- klai), so CREATE TABLE with FK(conversation_id) REFERENCES
-- widget_conversations(id) inside an alembic migration fails with 42501.
-- The migration's upgrade() is a no-op; all DDL lives here and runs as
-- klai superuser.
--
-- Apply with:
--   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB' \
--   < klai-portal/backend/alembic/versions/post_deploy_b7e4f1a9c3d2_conversation_quality_judgments_rls.sql"
--
-- Idempotent: every CREATE/ALTER uses IF NOT EXISTS so re-runs are safe.

BEGIN;

CREATE TABLE IF NOT EXISTS conversation_quality_judgments (
    id BIGSERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    -- Nullable + SET NULL (not CASCADE): the whole point of this table is
    -- to outlive the conversation it judged, anonymized. CASCADE would
    -- delete the row the moment widget_messages_retention purges the
    -- conversation, which is exactly when it should start its longer,
    -- anonymized life. The anonymization sweep (REQ-4) nulls `reasoning`
    -- before that purge runs; SET NULL then clears the now-dangling FK.
    conversation_id BIGINT REFERENCES widget_conversations(id) ON DELETE SET NULL,
    channel VARCHAR(16) NOT NULL DEFAULT 'webchat'
        CHECK (channel IN ('webchat')),
    outcome VARCHAR(24) NOT NULL
        CHECK (outcome IN ('resolved', 'partially_resolved', 'unresolved',
                            'escalated', 'out_of_scope', 'abandoned_early')),
    failure_category VARCHAR(24)
        CHECK (failure_category IN ('retrieval_miss', 'retrieval_wrong',
                                     'generation_error', 'policy_refusal',
                                     'scope_mismatch', 'user_confusion', 'none')),
    -- Evidence-grounded judge reasoning, may quote the conversation. Set to
    -- NULL by the anonymization sweep once the underlying conversation is
    -- purged (widget_messages_retention_days) — a quote from a deleted
    -- conversation is itself identifiable content, see SPEC-CHAT-QUALITY-
    -- LOOP-001 §6. anonymized_at records when that happened.
    reasoning TEXT,
    confidence VARCHAR(8) NOT NULL
        CHECK (confidence IN ('high', 'medium', 'low')),
    suggested_action TEXT,
    model_used VARCHAR(64) NOT NULL,
    judged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    anonymized_at TIMESTAMPTZ,
    CONSTRAINT uq_conversation_quality_judgments_conversation UNIQUE (conversation_id)
);
CREATE INDEX IF NOT EXISTS ix_conversation_quality_judgments_org_judged
    ON conversation_quality_judgments (org_id, judged_at DESC);
CREATE INDEX IF NOT EXISTS ix_conversation_quality_judgments_outcome
    ON conversation_quality_judgments (outcome);

-- Grant DML to portal_api so the runtime can SELECT / INSERT / UPDATE via
-- SQLAlchemy. RLS Cat-D enforces tenant isolation on top.
GRANT SELECT, INSERT, UPDATE, DELETE ON conversation_quality_judgments TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE conversation_quality_judgments_id_seq TO portal_api;

-- RLS Cat-D strict policy (per portal-security.md): every access path must
-- SET app.current_org_id; missing GUC = raise (helper returns NULL -> first
-- OR clause skipped, fallback strict check fires). Mirrors
-- widget_conversations / widget_messages.
ALTER TABLE conversation_quality_judgments ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_quality_judgments FORCE  ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON conversation_quality_judgments;
CREATE POLICY tenant_isolation ON conversation_quality_judgments
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

COMMIT;
