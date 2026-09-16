-- widget_conversations.is_test
--
-- Run as the 'klai' superuser (portal_api cannot ALTER klai-owned tables).
-- Apply after: alembic upgrade head stamps revision 2753d8303a76.
--
-- Reviewer-marked test conversation (PUT /api/app/activity/conversations/{id}/test,
-- SPEC-KNOWLEDGE-ACTIVITY-001): a colleague poking the widget to check an
-- answer, or trying to reproduce a bug report, is not real visitor traffic
-- and must drop out of the activity list, the queue count, the calibration
-- summary, the outcome loop, the nightly judge and the gap producer, exactly
-- like ``is_preview`` already does for an admin preview session. It is its
-- own column, not a second use of ``is_preview``: that column means one
-- specific thing (an admin's own preview session), and overloading it would
-- make every reader of ``is_preview`` implicitly also a reader of this flag.
--
-- Default false, backfilled false for every existing row: nothing already in
-- the table was ever marked as a test message.

BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'widget_conversations' AND column_name = 'is_test'
    ) THEN
        ALTER TABLE widget_conversations ADD COLUMN is_test BOOLEAN NOT NULL DEFAULT false;
    END IF;
END $$;

COMMIT;
