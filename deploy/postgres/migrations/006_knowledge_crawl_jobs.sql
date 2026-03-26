-- Migration: 006_knowledge_crawl_jobs.sql
-- Tracks web crawler job status.

CREATE TABLE IF NOT EXISTS knowledge.crawl_jobs (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL,
    kb_slug     TEXT NOT NULL,
    config      JSONB NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','completed','failed')),
    pages_total INTEGER NOT NULL DEFAULT 0,
    pages_done  INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  BIGINT NOT NULL,
    updated_at  BIGINT NOT NULL
);
