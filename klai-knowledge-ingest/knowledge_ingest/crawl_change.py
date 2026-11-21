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

``same_content`` is a second, coarser comparison used only to decide whether
a changed page is worth a new version. It ignores links, line order and
recurring site lines (chat widget text, cookie notice, fixed labels). It
never alters the stored text and never feeds the stored hash.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from urllib.parse import urlparse

import asyncpg

from knowledge_ingest import pg_store

_ANCHOR_ITEM = re.compile(r"^\s*(?:[*+-]\s+|\d+[.)]\s+)?\[[^\]]*\]\((?P<target>[^)\s]+)[^)]*\)\s*$")


def _is_in_page_anchor_item(line: str, page_base: str) -> bool:
    match = _ANCHOR_ITEM.match(line)
    if match is None:
        return False
    base, _, fragment = match["target"].partition("#")
    return bool(fragment) and (not base or base.rstrip("/") == page_base)


_LINK = re.compile(r"!?\[[^\]]*\]\([^)]*\)")

# A line seen on this many pages of one host is site chrome. Measured on 60 days
# of crawl history, out of sample: 10+ pages left 2119 rewrites, 5+ gave 2092
# and 3+ gave 2031, so the conservative threshold keeps nearly all of the gain.
SITE_LINE_MIN_PAGES = 10


def _mask_digits(line: str) -> str:
    return re.sub(r"\d+", "#", line)


def _page_lines(markdown: str, page_url: str) -> set[str]:
    """Lines of a page with links removed and whitespace collapsed, order and repeats gone."""
    page_base = page_url.partition("#")[0].rstrip("/")
    lines = (
        " ".join(_LINK.sub("", line).split())
        for line in markdown.splitlines()
        if not _is_in_page_anchor_item(line, page_base)
    )
    return {line for line in lines if line}


def learn_site_lines(pages: Iterable[tuple[str, str]]) -> dict[str, frozenset[str]]:
    """Digit-masked lines found on at least SITE_LINE_MIN_PAGES pages, per URL host."""
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for url, markdown in pages:
        counts[urlparse(url).netloc].update(
            {_mask_digits(line) for line in _page_lines(markdown, url)}
        )
    return {
        host: frozenset(line for line, n in counter.items() if n >= SITE_LINE_MIN_PAGES)
        for host, counter in counts.items()
    }


def same_content(
    stored_markdown: str, new_markdown: str, page_url: str, site_lines: frozenset[str]
) -> bool:
    def lines(markdown: str) -> set[str]:
        return {
            line for line in _page_lines(markdown, page_url) if _mask_digits(line) not in site_lines
        }

    return lines(stored_markdown) == lines(new_markdown)


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
    new_markdown: str,
    site_lines: frozenset[str],
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
    if stored_markdown is None:
        return False
    if (
        hashlib.sha256(stored_markdown.encode()).hexdigest() == stored_content_hash
        and crawl_change_hash(stored_markdown, url) == change_hash
    ):
        return True
    # The stored hash must be the change hash of the stored text, so a cleared
    # or purged hash cannot be mistaken for "same page".
    return crawl_change_hash(stored_markdown, url) == stored_content_hash and same_content(
        stored_markdown, new_markdown, url, site_lines
    )
