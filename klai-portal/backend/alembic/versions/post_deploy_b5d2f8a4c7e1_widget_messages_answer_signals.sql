-- Widget answer signals: per-answer retrieval certainty on widget_messages.
--
-- Run as the 'klai' superuser (portal_api cannot ALTER klai-owned tables).
-- Apply after: alembic upgrade head stamps revision b5d2f8a4c7e1.
--
-- * answer_signals — structured certainty captured next to an assistant
--   answer (top_score, band, gap_type, sources_count, refused, broad_mode,
--   language, model; SPEC-KNOWLEDGE-ACTIVITY-001 §4.1). The reranker score
--   and the firewall decision only exist while the answer is generated, so
--   calibrating visitor ratings against certainty is impossible unless the
--   signals are persisted with the answer.
--
-- No data backfill: every existing row starts with NULL signals.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'widget_messages' AND column_name = 'answer_signals'
    ) THEN
        ALTER TABLE widget_messages ADD COLUMN answer_signals JSONB;
    END IF;
END $$;

-- Signals describe an answer; a visitor turn has none to describe, and
-- keeping them off those rows stops a future writer from inventing them.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'widget_messages_answer_signals_assistant_only'
          AND conrelid = 'widget_messages'::regclass
    ) THEN
        ALTER TABLE widget_messages
            ADD CONSTRAINT widget_messages_answer_signals_assistant_only
            CHECK (answer_signals IS NULL OR role = 'assistant');
    END IF;
END $$;

COMMIT;
