"""
Async query helpers for the page_links link graph (SPEC-CRAWLER-003).

All queries are org- and kb-scoped for tenant isolation. Per
SPEC-TI-003-FOLLOWUP-001 AC-1 every helper takes the GUC-pinned
``asyncpg.Connection`` as its first argument; callers obtain it via
``tenant_scoped_connection(org_id)``.
"""

import asyncpg
import structlog

from knowledge_ingest.anchor_filter import SPREAD_LIMIT, filter_anchors

logger = structlog.get_logger()


async def get_outbound_urls(
    conn: asyncpg.Connection, url: str, org_id: str, kb_slug: str
) -> list[str]:
    """Return all URLs that `url` links to within this org/kb."""
    rows = await conn.fetch(
        "SELECT to_url FROM knowledge.page_links "
        "WHERE org_id = $1 AND kb_slug = $2 AND from_url = $3",
        org_id,
        kb_slug,
        url,
    )
    return [r["to_url"] for r in rows]


# Self-links (a language switcher or a "#" button resolves to the page itself:
# 423 and 276 pairs on the two measured sites) and texts that point at
# SPREAD_LIMIT or more distinct pages of the KB are removed here; the rest of the
# filter is anchor_filter.py. The spread subquery reads the KB's links once per
# call, which is fine at a few thousand links; an expression index on
# (org_id, kb_slug, lower(btrim(link_text))) is the upgrade for much larger KBs.
_ANCHOR_CANDIDATES = """
SELECT min(btrim(link_text)) AS text
FROM knowledge.page_links
WHERE org_id = $1 AND kb_slug = $2 AND to_url = $3
  AND btrim(coalesce(link_text, '')) <> ''
  AND split_part(to_url, '#', 1) <> split_part(from_url, '#', 1)
  AND lower(btrim(link_text)) NOT IN (
      SELECT lower(btrim(link_text)) FROM knowledge.page_links
      WHERE org_id = $1 AND kb_slug = $2 AND link_text IS NOT NULL
      GROUP BY 1 HAVING count(DISTINCT to_url) >= $4
  )
GROUP BY lower(btrim(link_text))
ORDER BY count(DISTINCT from_url) DESC, lower(btrim(link_text))
"""


async def get_anchor_texts(
    conn: asyncpg.Connection, url: str, org_id: str, kb_slug: str, page_text: str
) -> list[str]:
    """Return at most MAX_ANCHORS names other pages use for `url` that `page_text` lacks."""
    rows = await conn.fetch(_ANCHOR_CANDIDATES, org_id, kb_slug, url, SPREAD_LIMIT)
    anchors, dropped = filter_anchors((r["text"] for r in rows), page_text)
    logger.info(
        "anchor_texts_filtered",
        url=url,
        anchors_in=len(rows),
        kept=len(anchors),
        dropped=dict(dropped),
    )
    return anchors


async def get_incoming_count(conn: asyncpg.Connection, url: str, org_id: str, kb_slug: str) -> int:
    """Return the number of pages linking TO `url` within this org/kb."""
    count = await conn.fetchval(
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
    conn: asyncpg.Connection, org_id: str, kb_slug: str
) -> dict[str, int]:
    """DEPRECATED (SPEC-CRAWLER-005 REQ-05.1): No production caller since the
    two-phase pipeline makes incoming_link_count final at first Qdrant write.
    Kept for potential admin-only repair scripts. Do not re-introduce in the
    crawl path.

    Return a dict mapping each to_url to its incoming link count.
    """
    rows = await conn.fetch(
        "SELECT to_url, COUNT(*) AS cnt FROM knowledge.page_links "
        "WHERE org_id = $1 AND kb_slug = $2 "
        "GROUP BY to_url",
        org_id,
        kb_slug,
    )
    return {r["to_url"]: int(r["cnt"]) for r in rows}
