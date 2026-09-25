"""Decide whether a crawled page really changed since the last crawl.

``knowledge.crawled_pages.content_hash`` holds a change hash, not a hash of
the markdown itself. The markdown is still what gets indexed; only the hashed
projection drops noise that is not page text:

* In-page table-of-contents items: list items that consist of one link to a
  heading on the same page (``#fragment`` or the page URL plus a fragment).
  Some help sites render this block client-side and it is intermittently
  missing when crawl4ai snapshots the page, so the markdown oscillated between
  two shapes. Measured on 2026-09-25 over 45 days of crawl history for one
  help site: 63 of 130 content changes were this block or whitespace alone.
  The headings themselves stay in the body, so renaming one still counts.
* Whitespace: runs are collapsed, and padding inside link text is dropped.
"""

from __future__ import annotations

import hashlib
import re

import asyncpg

from knowledge_ingest import pg_store

_ANCHOR_ITEM = re.compile(r"^\s*(?:[*+-]\s+|\d+[.)]\s+)?\[[^\]]*\]\((?P<target>[^)\s]+)[^)]*\)\s*$")


def _is_in_page_anchor_item(line: str, page_base: str) -> bool:
    match = _ANCHOR_ITEM.match(line)
    if match is None:
        return False
    base, _, fragment = match["target"].partition("#")
    return bool(fragment) and (not base or base.rstrip("/") == page_base)


def crawl_change_hash(markdown: str, page_url: str) -> str:
    page_base = page_url.partition("#")[0].rstrip("/")
    kept = " ".join(
        line for line in markdown.splitlines() if not _is_in_page_anchor_item(line, page_base)
    )
    projection = " ".join(kept.split()).replace("[ ", "[").replace(" ]", "]")
    return hashlib.sha256(projection.encode()).hexdigest()


async def crawled_page_unchanged(
    conn: asyncpg.Connection,
    *,
    org_id: str,
    kb_slug: str,
    url: str,
    stored_content_hash: str | None,
    change_hash: str,
) -> bool:
    if stored_content_hash is None:
        return False
    if stored_content_hash == change_hash:
        return True
    # Rows written before the change hash existed hold sha256 of the raw
    # markdown. Project the stored markdown the same way so the first crawl
    # after the deploy compares like with like instead of re-ingesting every
    # page once. The markdown only counts when it is what that hash was taken
    # of: a cleared ('') or purged placeholder hash still forces a re-ingest.
    stored_markdown = await pg_store.get_crawled_page_markdown(conn, org_id, kb_slug, url)
    return (
        stored_markdown is not None
        and hashlib.sha256(stored_markdown.encode()).hexdigest() == stored_content_hash
        and crawl_change_hash(stored_markdown, url) == change_hash
    )
