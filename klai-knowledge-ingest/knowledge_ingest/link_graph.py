"""
Async query helpers for the page_links link graph (SPEC-CRAWLER-003).

All queries are org- and kb-scoped for tenant isolation.
"""

import asyncpg
import structlog

logger = structlog.get_logger()


async def get_outbound_urls(
    url: str, org_id: str, kb_slug: str, pool: asyncpg.Pool
) -> list[str]:
    """Return all URLs that `url` links to within this org/kb."""
    rows = await pool.fetch(
        "SELECT to_url FROM knowledge.page_links "
        "WHERE org_id = $1 AND kb_slug = $2 AND from_url = $3",
        org_id,
        kb_slug,
        url,
    )
    return [r["to_url"] for r in rows]


async def get_anchor_texts(
    url: str, org_id: str, kb_slug: str, pool: asyncpg.Pool
) -> list[str]:
    """Return non-empty anchor texts from pages linking TO `url`."""
    rows = await pool.fetch(
        "SELECT link_text FROM knowledge.page_links "
        "WHERE org_id = $1 AND kb_slug = $2 AND to_url = $3",
        org_id,
        kb_slug,
        url,
    )
    return [r["link_text"] for r in rows if r["link_text"] and r["link_text"].strip()]


async def get_incoming_count(
    url: str, org_id: str, kb_slug: str, pool: asyncpg.Pool
) -> int:
    """Return the number of pages linking TO `url` within this org/kb."""
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM knowledge.page_links "
        "WHERE org_id = $1 AND kb_slug = $2 AND to_url = $3",
        org_id,
        kb_slug,
        url,
    )
    return int(count or 0)


# @MX:WARN: [AUTO] compute_incoming_counts — deprecated post-crawl band-aid
# @MX:REASON: Deprecated band-aid — re-wiring into the crawl path creates a race
#   with enrichment. The two-phase pipeline (SPEC-CRAWLER-005) makes
#   incoming_link_count final at first Qdrant write via get_incoming_count() per-page.
async def compute_incoming_counts(
    org_id: str, kb_slug: str, pool: asyncpg.Pool
) -> dict[str, int]:
    """DEPRECATED (SPEC-CRAWLER-005 REQ-05.1): No production caller since the
    two-phase pipeline makes incoming_link_count final at first Qdrant write.
    Kept for potential admin-only repair scripts. Do not re-introduce in the
    crawl path.

    Return a dict mapping each to_url to its incoming link count.
    """
    rows = await pool.fetch(
        "SELECT to_url, COUNT(*) AS cnt FROM knowledge.page_links "
        "WHERE org_id = $1 AND kb_slug = $2 "
        "GROUP BY to_url",
        org_id,
        kb_slug,
    )
    return {r["to_url"]: int(r["cnt"]) for r in rows}
