-- Migration: 011_knowledge_crawl_registry.sql
-- Crawl registry: URL dedup, raw markdown cache, and link graph for knowledge-ingest.
-- crawled_pages: one row per (org_id, kb_slug, url) — upserted on every successful crawl.
-- page_links:    one row per (org_id, kb_slug, from_url, to_url) — upserted per crawled page.

CREATE TABLE IF NOT EXISTS knowledge.crawled_pages (
    id           BIGSERIAL PRIMARY KEY,
    org_id       TEXT    NOT NULL,
    kb_slug      TEXT    NOT NULL,
    url          TEXT    NOT NULL,
    content_hash TEXT    NOT NULL,
    raw_markdown TEXT    NOT NULL,
    crawled_at   BIGINT  NOT NULL,
    CONSTRAINT crawled_pages_uniq UNIQUE (org_id, kb_slug, url)
);

CREATE INDEX IF NOT EXISTS idx_crawled_pages_lookup
    ON knowledge.crawled_pages (org_id, kb_slug, url);

CREATE TABLE IF NOT EXISTS knowledge.page_links (
    id        BIGSERIAL PRIMARY KEY,
    org_id    TEXT NOT NULL,
    kb_slug   TEXT NOT NULL,
    from_url  TEXT NOT NULL,
    to_url    TEXT NOT NULL,
    link_text TEXT NOT NULL DEFAULT '',
    CONSTRAINT page_links_uniq UNIQUE (org_id, kb_slug, from_url, to_url)
);

CREATE INDEX IF NOT EXISTS idx_page_links_outgoing
    ON knowledge.page_links (org_id, kb_slug, from_url);

CREATE INDEX IF NOT EXISTS idx_page_links_incoming
    ON knowledge.page_links (org_id, kb_slug, to_url);
