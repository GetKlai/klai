-- Post-deploy SQL for revision c2a7e9d4b1f6 (answer_reviews).
-- SPEC-KNOWLEDGE-ACTIVITY-001 §4.2/§4.4.
--
-- portal_api lacks REFERENCES privilege on `widget_conversations`,
-- `widget_messages`, `portal_users` and `portal_retrieval_gaps` (all owned by
-- klai), so CREATE TABLE with those foreign keys inside an alembic migration
-- fails with 42501. The migration's upgrade() is a no-op; all DDL lives here
-- and runs as klai superuser.
--
-- Apply with:
--   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB' \
--   < klai-portal/backend/alembic/versions/post_deploy_c2a7e9d4b1f6_answer_reviews_rls.sql"
--
-- Idempotent: every CREATE/ALTER uses IF NOT EXISTS so re-runs are safe.

BEGIN;

CREATE TABLE IF NOT EXISTS answer_reviews (
    id BIGSERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
    channel VARCHAR(16) NOT NULL DEFAULT 'webchat'
        CHECK (channel IN ('webchat', 'librechat')),
    -- Nullable + SET NULL (not CASCADE), same reason as
    -- conversation_quality_judgments: a review must outlive the 7-day
    -- widget_messages_retention_days purge of the conversation it judged.
    -- CASCADE would delete the review at the exact moment it becomes the only
    -- remaining record that this answer was checked by a human; SET NULL
    -- instead clears the FK once the conversation/message rows are purged.
    conversation_id BIGINT REFERENCES widget_conversations(id) ON DELETE SET NULL,
    message_id BIGINT REFERENCES widget_messages(id) ON DELETE SET NULL,
    -- LibreChat lives in a per-tenant MongoDB, so there is no local row to
    -- foreign-key against; the Mongo ObjectId string anchors those rows.
    external_message_id TEXT,
    -- Kept after the purge so the review can still be placed in the
    -- (now-deleted) transcript without keeping the transcript itself.
    turn_sequence INTEGER NOT NULL,
    -- Nullable + SET NULL: a colleague who leaves must not take their
    -- reviews, or the gap history behind them, with them. CASCADE here would
    -- erase the knowledge-side audit trail of a departed reviewer.
    reviewer_user_id INTEGER REFERENCES portal_users(id) ON DELETE SET NULL,
    verdict VARCHAR(16) NOT NULL
        CHECK (verdict IN ('correct', 'incomplete', 'wrong', 'not_a_fault')),
    cause VARCHAR(24) NOT NULL
        CHECK (cause IN ('knowledge_missing', 'knowledge_wrong', 'behaviour', 'none')),
    -- 'cause' only carries meaning for a bad answer, so the pair is one fact:
    -- without this a stale default 'none' on incomplete/wrong (or a cause on a
    -- correct answer) silently skews every gap count derived from this table.
    CHECK ((verdict IN ('correct', 'not_a_fault') AND cause = 'none') OR (verdict IN ('incomplete', 'wrong') AND cause <> 'none')),
    note TEXT,
    kb_slug TEXT,
    -- Retrieval-state snapshots taken at review time. The judge outcome and
    -- the confidence band are recomputed nightly and the KB moves under them,
    -- so storing only the live values would rewrite history: a review filed
    -- against a 'low' band must still explain itself after the same query
    -- later scores 'high'. Retention on the source rows is 7 days.
    band_at_review VARCHAR(8) NOT NULL
        CHECK (band_at_review IN ('high', 'medium', 'low', 'unknown')),
    judge_outcome_at_review VARCHAR(24),
    judge_failure_category_at_review VARCHAR(24),
    language VARCHAR(8),
    gap_id BIGINT REFERENCES portal_retrieval_gaps(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partial uniques, not table constraints: SET NULL deliberately leaves many
-- rows with a NULL message_id / external_message_id after a purge, and those
-- must not collide with each other.
CREATE UNIQUE INDEX IF NOT EXISTS uq_answer_reviews_message
    ON answer_reviews (message_id)
    WHERE message_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_answer_reviews_external_message
    ON answer_reviews (external_message_id)
    WHERE external_message_id IS NOT NULL;
-- Review lists are always per tenant, newest first.
CREATE INDEX IF NOT EXISTS ix_answer_reviews_org_reviewed
    ON answer_reviews (org_id, reviewed_at DESC);
CREATE INDEX IF NOT EXISTS ix_answer_reviews_conversation
    ON answer_reviews (conversation_id);

-- Grant DML to portal_api so the runtime can SELECT / INSERT / UPDATE via
-- SQLAlchemy. RLS Cat-D enforces tenant isolation on top.
GRANT SELECT, INSERT, UPDATE, DELETE ON answer_reviews TO portal_api;
GRANT USAGE, SELECT ON SEQUENCE answer_reviews_id_seq TO portal_api;

-- RLS Cat-D strict policy (per portal-security.md): every access path must
-- SET app.current_org_id; missing GUC = raise (helper returns NULL -> first
-- OR clause skipped, fallback strict check fires). Mirrors
-- conversation_quality_judgments / widget_messages.
ALTER TABLE answer_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE answer_reviews FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON answer_reviews;
CREATE POLICY tenant_isolation ON answer_reviews
    USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())
    WITH CHECK (org_id = _rls_current_org_id());

COMMIT;
