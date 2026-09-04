-- Widget feedback: rating + turn_id columns on widget_messages.
--
-- Run as the 'klai' superuser (portal_api cannot ALTER klai-owned tables).
-- Apply after: alembic upgrade head stamps revision f4a8c2e6b1d9.
--
-- * turn_id — client-generated per-turn identifier that the widget chat
--   request carries (``widget_turn_id``). record_widget_turn stores it on
--   the assistant row; POST /partner/v1/widget/feedback uses it to address
--   the answer the visitor rated, without ever exposing the row id.
-- * rating — visitor thumbs rating for an assistant answer
--   ('thumbsUp' / 'thumbsDown' / NULL = not rated or withdrawn).
--
-- No data backfill: every existing row starts unrated with NULL turn_id.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'widget_messages' AND column_name = 'turn_id'
    ) THEN
        ALTER TABLE widget_messages ADD COLUMN turn_id VARCHAR(64);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'widget_messages' AND column_name = 'rating'
    ) THEN
        ALTER TABLE widget_messages ADD COLUMN rating VARCHAR(16);
    END IF;
END $$;

-- Feedback addresses exactly one assistant row; the client id must be
-- globally unique among rated-able turns. Partial so the many user rows
-- (and history predating this column) keep NULL turn_id without colliding.
CREATE UNIQUE INDEX IF NOT EXISTS uq_widget_messages_turn_id
    ON widget_messages (turn_id)
    WHERE turn_id IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'widget_messages_rating_values'
          AND conrelid = 'widget_messages'::regclass
    ) THEN
        ALTER TABLE widget_messages
            ADD CONSTRAINT widget_messages_rating_values
            CHECK (rating IS NULL OR rating IN ('thumbsUp', 'thumbsDown'));
    END IF;
END $$;

-- Only assistant answers can carry a rating.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'widget_messages_rating_assistant_only'
          AND conrelid = 'widget_messages'::regclass
    ) THEN
        ALTER TABLE widget_messages
            ADD CONSTRAINT widget_messages_rating_assistant_only
            CHECK (rating IS NULL OR role = 'assistant');
    END IF;
END $$;

COMMIT;
