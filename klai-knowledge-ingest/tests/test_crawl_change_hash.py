"""Crawled pages are only re-ingested when their text really changed.

A client-rendered in-page table of contents (list items linking to headings
on the same page) sometimes renders before crawl4ai snapshots the page and
sometimes does not, so the markdown oscillates between two shapes and every
other crawl used to re-ingest the page. The change hash ignores that block
and whitespace-only differences; everything else still counts.

All pages here are synthetic.
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.crawl_change import crawl_change_hash

URL = "https://help.example.com/widgets"

BODY = (
    "# Widgets\n\n"
    "Widgets connect the front panel to the back office. "
    "A standard widget costs 12.50 per month.\n\n"
    "## Installing a widget\n\n"
    "Plug the widget into port A and wait for the green light.\n\n"
    "## Removing a widget\n\n"
    "Unplug the widget and return it within 30 days.\n"
)

TOC = (
    f"  * [Installing a widget ]({URL}#block-1a2b)\n"
    f"  * [Removing a widget]({URL}#block-3c4d)\n"
    "  * [Back to top](#top)\n"
)

WITH_TOC = BODY.replace("## Installing", TOC + "\n## Installing", 1)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_page_differing_only_in_toc_anchor_block_has_same_change_hash() -> None:
    assert crawl_change_hash(WITH_TOC, URL) == crawl_change_hash(BODY, URL)


def test_whitespace_only_difference_has_same_change_hash() -> None:
    reflowed = BODY.replace("\n\n", "\n\n\n").replace(". A standard", ".  A standard")
    assert crawl_change_hash(reflowed, URL) == crawl_change_hash(BODY, URL)


def test_real_text_change_has_different_change_hash() -> None:
    changed = BODY.replace("12.50", "12.95")
    assert crawl_change_hash(changed, URL) != crawl_change_hash(BODY, URL)


def test_links_to_other_pages_still_count() -> None:
    with_related = BODY + "\n  * [Cables](https://help.example.com/cables#setup)\n"
    assert crawl_change_hash(with_related, URL) != crawl_change_hash(BODY, URL)


# ---------------------------------------------------------------------------
# _ingest_crawl_result
# ---------------------------------------------------------------------------


def _crawl_result(markdown: str, html: str) -> CrawlResult:
    return CrawlResult(
        url=URL,
        fit_markdown=markdown,
        raw_markdown=markdown,
        html=html,
        word_count=len(markdown.split()),
        success=True,
        links={"internal": []},
        response_headers={},
        metadata={},
    )


def _conn(stored_markdown: str | None) -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(
        return_value=None if stored_markdown is None else {"raw_markdown": stored_markdown}
    )

    @asynccontextmanager
    async def _tx():
        yield None

    conn.transaction = MagicMock(side_effect=_tx)
    return conn


async def _crawl(markdown: str, stored: tuple[str, str], stored_markdown: str | None):
    with (
        patch("knowledge_ingest.pg_store.upsert_crawled_page", new_callable=AsyncMock) as upsert,
        patch("knowledge_ingest.pg_store.upsert_page_links", new_callable=AsyncMock),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
            return_value={"status": "ok", "chunks": 1},
        ) as ingest,
    ):
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            _conn(stored_markdown),
            _crawl_result(markdown, f"<html>{len(markdown)}</html>"),
            URL,
            "org1",
            "kb1",
            stored=stored,
        )
    return ingest, upsert


@pytest.mark.asyncio
async def test_toc_oscillation_does_not_reingest() -> None:
    ingest, upsert = await _crawl(
        BODY, ("old-html", crawl_change_hash(WITH_TOC, URL)), stored_markdown=WITH_TOC
    )

    ingest.assert_not_called()
    assert upsert.call_args.kwargs["content_hash"] == crawl_change_hash(BODY, URL)


@pytest.mark.asyncio
async def test_real_change_reingests() -> None:
    changed = BODY.replace("12.50", "12.95")
    ingest, upsert = await _crawl(
        changed, ("old-html", crawl_change_hash(BODY, URL)), stored_markdown=BODY
    )

    ingest.assert_called_once()
    assert upsert.call_args.kwargs["content_hash"] == crawl_change_hash(changed, URL)


@pytest.mark.asyncio
async def test_first_run_after_deploy_does_not_reingest_unchanged_pages() -> None:
    """Rows written before the change hash hold sha256 of the raw markdown.

    The first crawl after the deploy must compare like with like: the stored
    raw markdown is projected the same way as the new crawl, so an unchanged
    page (or one that only flipped its TOC block) is not re-ingested, and the
    row is upgraded to the change hash.
    """
    ingest, upsert = await _crawl(BODY, ("old-html", _sha256(WITH_TOC)), stored_markdown=WITH_TOC)

    ingest.assert_not_called()
    assert upsert.call_args.kwargs["content_hash"] == crawl_change_hash(BODY, URL)


@pytest.mark.asyncio
async def test_first_run_after_deploy_reingests_real_change() -> None:
    changed = WITH_TOC.replace("12.50", "12.95")
    ingest, _ = await _crawl(changed, ("old-html", _sha256(WITH_TOC)), stored_markdown=WITH_TOC)

    ingest.assert_called_once()


@pytest.mark.asyncio
async def test_purged_hash_still_forces_reingest() -> None:
    """A purged placeholder hash does not match the stored markdown, so the page re-ingests."""
    ingest, _ = await _crawl(BODY, ("old-html", "__login_wall_purged__"), stored_markdown=BODY)

    ingest.assert_called_once()
