-- Empties portal_gap_kept_questions.
--
-- The code that wrote, read and purged this table is gone: widget messages now
-- live for the organisation's retention term (90 days by default), longer than
-- the four weeks a kept question bridged, so the knowledge-gap list reads every
-- turn from the messages. The table itself is dropped by a later change,
-- because this script runs before the new container starts and the previous
-- release still reads the table. Until then nothing would remove the rows it
-- already holds (masked visitor questions), so they are removed here.
--
-- Safe with the previous release still running: it only loses kept rows, which
-- it treats as an empty result. Idempotent, and a no-op once the table is gone.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.portal_gap_kept_questions') IS NOT NULL THEN
        DELETE FROM public.portal_gap_kept_questions;
    END IF;
END
$$;

COMMIT;
