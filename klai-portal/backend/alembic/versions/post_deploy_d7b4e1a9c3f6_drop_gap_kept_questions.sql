-- Drops portal_gap_kept_questions.
--
-- Nothing reads or writes the table since #2352: widget messages live for the
-- organisation's retention term, so the knowledge-gap list reads its turns
-- from the messages. The release before this one already runs without the
-- table's code, so an automatic rollback (image-only) lands on code that does
-- not miss it. The two scripts of revision c8e2f5a1d3b7 that gave the table
-- its owner, policy and grants, and that emptied it, are removed in the same
-- change: they run on every deploy and would fail on a table that is gone.
--
-- Run as klai AFTER alembic upgrade head. Idempotent.

BEGIN;

DROP TABLE IF EXISTS public.portal_gap_kept_questions;

COMMIT;
