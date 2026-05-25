-- REQ-16 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 Finding B-14, MED):
-- 1. Add widgets.deleted_at column for soft-delete (AC16.1).
-- 2. Replace widget_conversations.widget_id CASCADE FK with NO ACTION so a
--    hand-bypass DELETE FROM widgets cannot wipe the audit trail (AC16.4).
--
-- Run as the 'klai' superuser (portal_api cannot ALTER klai-owned tables).
-- Apply after: alembic upgrade head stamps revision 874c2c370830.

-- 1) widgets.deleted_at (idempotent).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'widgets'
          AND column_name = 'deleted_at'
    ) THEN
        ALTER TABLE widgets
            ADD COLUMN deleted_at TIMESTAMPTZ NULL;
    END IF;
END $$;

-- Index supports the active-only lookups in admin + partner code.
CREATE INDEX IF NOT EXISTS ix_widgets_active
    ON widgets (org_id)
    WHERE deleted_at IS NULL;

-- 2) Replace CASCADE FK on widget_conversations.widget_id with NO ACTION so
--    an out-of-band DELETE FROM widgets cannot orphan or wipe the audit
--    trail. The current production FK name is the SQLAlchemy default
--    "widget_conversations_widget_id_fkey"; the DO block tolerates a
--    different name by looking it up via pg_constraint.
DO $$
DECLARE
    fk_name text;
BEGIN
    SELECT conname INTO fk_name
    FROM pg_constraint
    WHERE conrelid = 'widget_conversations'::regclass
      AND contype  = 'f'
      AND confrelid = 'widgets'::regclass
      AND confdeltype = 'c';  -- only when current rule is CASCADE
    IF fk_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE widget_conversations DROP CONSTRAINT %I', fk_name);
        ALTER TABLE widget_conversations
            ADD CONSTRAINT widget_conversations_widget_id_fkey
            FOREIGN KEY (widget_id) REFERENCES widgets (id)
            ON DELETE NO ACTION;
    END IF;
END $$;
