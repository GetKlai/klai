-- 012: Add raw_html_hash column to crawled_pages for dual-hash deduplication.
--
-- Dual-hash strategy:
--   raw_html_hash  = SHA-256 of the raw fetched HTML (before extraction pipeline)
--   content_hash   = SHA-256 of the extracted markdown (after crawl4ai / html2text)
--
-- Skip logic (in order):
--   1. raw_html_hash unchanged  → skip everything (page is identical at source level)
--   2. raw_html_hash changed, content_hash unchanged
--                               → HTML rearranged (JS, tracking pixel, cache-buster)
--                                 but article content is the same → update raw_html_hash,
--                                 skip re-ingest
--   3. both changed             → real content change → full re-ingest
--
-- WARNING (pipeline config change): modifying the crawl4ai extraction settings
-- (excluded_tags, PruningContentFilter threshold, JS removal scripts) changes
-- content_hash for every page even when the actual page content has not changed.
-- After such a change, force a full re-ingest by clearing content_hash:
--   UPDATE knowledge.crawled_pages
--      SET content_hash = ''
--    WHERE org_id = '<org>' AND kb_slug = '<slug>';

ALTER TABLE knowledge.crawled_pages
    ADD COLUMN IF NOT EXISTS raw_html_hash text;
