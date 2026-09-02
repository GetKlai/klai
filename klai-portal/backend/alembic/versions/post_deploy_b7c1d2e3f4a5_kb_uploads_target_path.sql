-- kb_uploads.target_path — replace an uploaded knowledge-base source.
--
-- Run as the 'klai' superuser (portal_api cannot ALTER klai-owned tables;
-- post_deploy_85e5d0a7cb98_kb_uploads_rls.sql transferred ownership so the
-- table could FORCE ROW LEVEL SECURITY).
-- Apply BEFORE the portal-api image that reads this column starts: the ORM
-- selects target_path on every kb_uploads query, so a container that boots
-- against the old schema 500s on the Sources tab.
--
-- A normal upload ingests under path = source_ref, the sha256 of its own
-- bytes. That makes every re-upload of a CHANGED file a brand new document
-- key, so the old source stays live next to the new one — the reason users
-- deleted the source and added it again to update it.
--
-- target_path overrides the document key for one upload: it holds the path
-- of the source being replaced. knowledge-ingest's ingest_document already
-- supersedes the active artifact under a path (soft-delete + create +
-- superseded_by + Qdrant clear), so ingesting under the original path IS
-- the replace — one row, no gap, no duplicate.
--
-- NULL for normal uploads, which keeps their path = source_ref behaviour
-- untouched.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'kb_uploads'
          AND column_name = 'target_path'
    ) THEN
        ALTER TABLE public.kb_uploads
            ADD COLUMN target_path VARCHAR(128);
    END IF;
END $$;

-- The poller and the sources list both look rows up by the path they are
-- about to overwrite, always scoped to one KB.
CREATE INDEX IF NOT EXISTS ix_kb_uploads_target_path
    ON public.kb_uploads (org_id, kb_id, target_path)
    WHERE target_path IS NOT NULL;
