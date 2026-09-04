-- Post-deploy SQL for revision d9e0f1a2b3c4:
-- outcome label on widget_conversations.
--
-- Run as the 'klai' superuser (portal_api cannot ALTER klai-owned tables —
-- see the alembic-cannot-drop-non-portal_api-tables pitfall and the sibling
-- no-op marker migration).
--
-- * outcome — heuristic conversation outcome written by the widget-outcome
--   background loop (app/services/widget_outcome.py):
--   'resolved' / 'escalated' / 'abandoned' / 'unknown'.
--   NULL = not yet determined (conversation still active or not yet
--   processed). CHECK only rejects FALSE, so NULL stays allowed.
--
-- No data backfill: every existing row starts NULL and the loop labels it
-- on the next pass once the conversation has been quiet long enough.
--
-- Idempotency: guarded ADD COLUMN / ADD CONSTRAINT, CREATE INDEX IF NOT
-- EXISTS — safe to re-run.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'widget_conversations' AND column_name = 'outcome'
    ) THEN
        ALTER TABLE widget_conversations ADD COLUMN outcome VARCHAR(16);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_widget_conversations_outcome'
          AND conrelid = 'widget_conversations'::regclass
    ) THEN
        ALTER TABLE widget_conversations
            ADD CONSTRAINT ck_widget_conversations_outcome
            CHECK (outcome IS NULL OR outcome IN ('resolved', 'escalated', 'abandoned', 'unknown'));
    END IF;
END $$;

-- Drives the outcome loop's candidate scan (unlabelled, quiet-enough rows
-- per tenant). Partial so labelled rows drop out of the index entirely.
CREATE INDEX IF NOT EXISTS ix_widget_conversations_outcome_pending
    ON widget_conversations (org_id, last_message_at)
    WHERE outcome IS NULL AND is_preview = false;

COMMIT;
