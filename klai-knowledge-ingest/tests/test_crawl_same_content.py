"""Coarser comparison for the write decision: links, line order and recurring site lines.

All pages here are synthetic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from knowledge_ingest.crawl_change import (
    crawl_change_hash,
    crawled_page_unchanged,
    learn_site_lines,
)

URL = "https://help.example.com/guide/setup"

BODY = (
    "# Setup guide\n\n"
    "Connect the base station to the power supply.\n\n"
    "Press the pairing button for three seconds.\n"
)

RELATED_A = (
    "* [Cables](https://help.example.com/cables)\n"
    "* [Adapters](https://help.example.com/adapters)\n"
    "* [Batteries](https://help.example.com/batteries)\n"
)
RELATED_B = (
    "* [Chargers](https://help.example.com/chargers)\n* [Stands](https://help.example.com/stands)\n"
)

STORED = BODY + "\n" + RELATED_A
SITE_LINE = "Chat with us, we are online 24 hours"


async def _unchanged(stored: str, new: str, stored_hash: str | None, site_lines=frozenset()):
    with patch(
        "knowledge_ingest.pg_store.get_crawled_page_markdown",
        new_callable=AsyncMock,
        return_value=stored,
    ):
        return await crawled_page_unchanged(
            AsyncMock(),
            org_id="org1",
            kb_slug="kb1",
            url=URL,
            stored_content_hash=stored_hash,
            change_hash=crawl_change_hash(new, URL),
            new_markdown=new,
            site_lines=site_lines,
        )


def _site_lines(pages_with_line: int) -> frozenset[str]:
    pages = [
        (f"https://help.example.com/p{i}", f"# Page {i}\n\nText {i}.\n\n{SITE_LINE}\n")
        for i in range(pages_with_line)
    ]
    return learn_site_lines(pages).get("help.example.com", frozenset())


@pytest.mark.asyncio
async def test_other_links_in_other_order_are_unchanged() -> None:
    new = BODY + "\n" + RELATED_B + "\nPress the pairing button for three seconds.\n"
    assert await _unchanged(STORED, new, crawl_change_hash(STORED, URL)) is True


@pytest.mark.asyncio
async def test_recurring_site_line_difference_is_unchanged_from_ten_pages() -> None:
    stored = STORED + f"\n{SITE_LINE}\n"
    new = STORED + f"\n{SITE_LINE.replace('24', '12')}\n"
    stored_hash = crawl_change_hash(stored, URL)
    assert await _unchanged(stored, new, stored_hash, _site_lines(10)) is True
    assert await _unchanged(stored, new, stored_hash, _site_lines(9)) is False


@pytest.mark.asyncio
async def test_changed_sentence_is_changed() -> None:
    new = STORED.replace("three seconds", "five seconds")
    assert await _unchanged(STORED, new, crawl_change_hash(STORED, URL)) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_hash", ["", "__login_wall_purged__", None])
async def test_cleared_or_purged_hash_still_forces_reingest(stored_hash: str | None) -> None:
    new = BODY + "\n" + RELATED_B
    assert await _unchanged(STORED, new, stored_hash) is False
