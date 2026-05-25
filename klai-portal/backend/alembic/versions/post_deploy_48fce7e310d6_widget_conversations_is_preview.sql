-- REQ-15 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 Finding B-11, MED):
-- Add widget_conversations.is_preview boolean column so admin preview-tests
-- can be flagged and excluded from visitor-facing stats aggregates.
--
-- Run as the 'klai' superuser (portal_api cannot ALTER klai-owned tables).
-- Apply after: alembic upgrade head stamps revision 48fce7e310d6.
--
-- Default false: every existing row is treated as a real visitor session.
-- Subsequent preview-session JWTs (is_preview=true claim) flip the bit on
-- newly-created conversation rows.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'widget_conversations'
          AND column_name = 'is_preview'
    ) THEN
        ALTER TABLE widget_conversations
            ADD COLUMN is_preview BOOLEAN NOT NULL DEFAULT false;
    END IF;
END $$;

-- Helpful for queries that filter on is_preview (the stats aggregates do).
CREATE INDEX IF NOT EXISTS ix_widget_conversations_is_preview
    ON widget_conversations (widget_id, is_preview)
    WHERE is_preview = false;
