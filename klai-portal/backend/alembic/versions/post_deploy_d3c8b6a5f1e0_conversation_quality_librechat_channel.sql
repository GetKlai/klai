-- Post-deploy SQL for revision d3c8b6a5f1e0 (LibreChat channel support on
-- conversation_quality_judgments). SPEC-CHAT-QUALITY-LOOP-001 REQ-5.
--
-- Apply with:
--   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB' \
--   < klai-portal/backend/alembic/versions/post_deploy_d3c8b6a5f1e0_conversation_quality_librechat_channel.sql"
--
-- Idempotent: guarded with IF NOT EXISTS / DO blocks so re-runs are safe.

BEGIN;

ALTER TABLE conversation_quality_judgments
    ADD COLUMN IF NOT EXISTS external_conversation_id TEXT;

-- One judgment per LibreChat conversation, same idempotency guarantee the
-- existing UNIQUE(conversation_id) gives webchat rows. NULLs (all webchat
-- rows) are not considered duplicates by Postgres, so this coexists cleanly.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'uq_conversation_quality_judgments_external_conversation'
    ) THEN
        ALTER TABLE conversation_quality_judgments
            ADD CONSTRAINT uq_conversation_quality_judgments_external_conversation
            UNIQUE (external_conversation_id);
    END IF;
END $$;

-- Widen channel CHECK to allow 'librechat'. Postgres has no ADD CONSTRAINT
-- IF NOT EXISTS for CHECK, so drop-then-add inside the same transaction —
-- safe: the OLD constraint only accepted 'webchat', which is a subset of
-- the new allowed set, so no existing row can violate the replacement.
--
-- The original CREATE TABLE (post_deploy_b7e4f1a9c3d2) declared this CHECK
-- inline with no explicit name, so Postgres auto-named it
-- conversation_quality_judgments_channel_check — NOT ck_cqj_channel (that
-- name only exists in the SQLAlchemy ORM model's CheckConstraint(), which
-- was never applied as DDL since portal_api doesn't own this table). Drop
-- the REAL auto-generated name; ck_cqj_channel here is the constraint this
-- script is about to (re-)create, not one to look for.
ALTER TABLE conversation_quality_judgments
    DROP CONSTRAINT IF EXISTS conversation_quality_judgments_channel_check;
ALTER TABLE conversation_quality_judgments DROP CONSTRAINT IF EXISTS ck_cqj_channel;
ALTER TABLE conversation_quality_judgments
    ADD CONSTRAINT ck_cqj_channel CHECK (channel IN ('webchat', 'librechat'));

COMMIT;
