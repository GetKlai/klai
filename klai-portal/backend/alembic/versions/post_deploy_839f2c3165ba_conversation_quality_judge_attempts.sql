-- Post-deploy SQL for revision 839f2c3165ba (bounded judge retry loop).
--
-- portal_api lacks ALTER privilege on conversation_quality_judgments (owned
-- by klai). The migration's upgrade() is a no-op; all DDL lives here and
-- runs as klai superuser.
--
-- Apply with:
--   ssh core-01 "docker exec klai-core-postgres-1 sh -c \
--       'psql -U \$POSTGRES_USER -d \$POSTGRES_DB' \
--   < klai-portal/backend/alembic/versions/post_deploy_839f2c3165ba_conversation_quality_judge_attempts.sql"
--
-- Idempotent: every ALTER/DO block is guarded so re-runs are safe.

BEGIN;

-- A row can now represent a conversation that has only ever failed to be
-- judged (parse or LLM-call failure) — outcome/confidence/judged_at are
-- NULL until the first successful verdict lands.
ALTER TABLE conversation_quality_judgments ALTER COLUMN outcome DROP NOT NULL;
ALTER TABLE conversation_quality_judgments ALTER COLUMN confidence DROP NOT NULL;
ALTER TABLE conversation_quality_judgments ALTER COLUMN judged_at DROP NOT NULL;
ALTER TABLE conversation_quality_judgments ALTER COLUMN judged_at DROP DEFAULT;

-- Widen the two CHECKs to allow NULL; the allowed non-NULL values are
-- unchanged, so no existing row can violate either replacement.
ALTER TABLE conversation_quality_judgments
    DROP CONSTRAINT IF EXISTS conversation_quality_judgments_outcome_check;
ALTER TABLE conversation_quality_judgments DROP CONSTRAINT IF EXISTS ck_cqj_outcome;
ALTER TABLE conversation_quality_judgments
    ADD CONSTRAINT ck_cqj_outcome CHECK (
        outcome IS NULL OR outcome IN
        ('resolved', 'partially_resolved', 'unresolved', 'escalated', 'out_of_scope', 'abandoned_early')
    );

ALTER TABLE conversation_quality_judgments
    DROP CONSTRAINT IF EXISTS conversation_quality_judgments_confidence_check;
ALTER TABLE conversation_quality_judgments DROP CONSTRAINT IF EXISTS ck_cqj_confidence;
ALTER TABLE conversation_quality_judgments
    ADD CONSTRAINT ck_cqj_confidence CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low'));

-- Attempt bookkeeping. failed_attempts counts consecutive failed passes
-- since the row was created; conversation_judge._MAX_JUDGE_ATTEMPTS (3)
-- is the cutoff the selection queries apply. last_attempted_at is set on
-- every attempt (success or failure); judged_at stays success-only.
ALTER TABLE conversation_quality_judgments
    ADD COLUMN IF NOT EXISTS failed_attempts SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE conversation_quality_judgments
    ADD COLUMN IF NOT EXISTS last_attempted_at TIMESTAMPTZ;

COMMIT;
