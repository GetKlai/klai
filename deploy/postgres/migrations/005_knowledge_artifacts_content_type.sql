-- Migration: 005_knowledge_artifacts_content_type.sql
-- Adds content_type and extra fields to knowledge.artifacts.
-- Safe: additive columns with defaults, no data loss.

ALTER TABLE knowledge.artifacts
    ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'unknown';

ALTER TABLE knowledge.artifacts
    ADD COLUMN IF NOT EXISTS extra JSONB NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_artifacts_content_type
    ON knowledge.artifacts(content_type);
