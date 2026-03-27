-- Migration 009: per-KB config (visibility) with TTL-cache support
-- Run: psql -d klai -f 009_knowledge_kb_config.sql

CREATE TABLE IF NOT EXISTS knowledge.kb_config (
    org_id      TEXT NOT NULL,
    kb_slug     TEXT NOT NULL,
    visibility  TEXT NOT NULL DEFAULT 'internal',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, kb_slug)
);

-- NOTIFY trigger so kb_config.py can evict its TTL cache immediately on change
CREATE OR REPLACE FUNCTION knowledge.notify_kb_config_changed()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('kb_config_changed', NEW.org_id || ':' || NEW.kb_slug);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kb_config_changed_trigger ON knowledge.kb_config;
CREATE TRIGGER kb_config_changed_trigger
    AFTER INSERT OR UPDATE ON knowledge.kb_config
    FOR EACH ROW EXECUTE FUNCTION knowledge.notify_kb_config_changed();
