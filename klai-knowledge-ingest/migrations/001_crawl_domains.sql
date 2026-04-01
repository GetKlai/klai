-- Migration: create knowledge.crawl_domains
-- Persistent CSS selectors per domain per org, used by the crawl wizard.
-- SPEC-CRAWL-001 / R-2, R-3

CREATE TABLE IF NOT EXISTS knowledge.crawl_domains (
    domain          TEXT        NOT NULL,
    org_id          TEXT        NOT NULL,
    css_selector    TEXT        NOT NULL,
    selector_source TEXT        NOT NULL CHECK (selector_source IN ('user', 'ai')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (domain, org_id)
);

COMMENT ON TABLE knowledge.crawl_domains IS
    'Persistent CSS selectors per domain per org, used by the crawl wizard.';
