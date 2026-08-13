"""Dead-entry-point fallback: seed the crawl from a validated interior page.

A client-rendered homepage/hub (support.ascendcloud.com) yields no ingestable
pages because the crawler can't follow its links — they arrive as unrendered
template tokens. When the operator validated a specific article in the preview,
``run_crawl_job`` retries the crawl from that ``discovery_seed_url`` instead,
whose links DO render, so BFS discovers the rest of the site.

These tests pin the contract: fall back only on a genuinely empty primary
crawl, seed from the interior page, and don't pay for it on a healthy site.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.adapters.crawler import run_crawl_job
from knowledge_ingest.crawl4ai_client import CrawlResult

HUB = "https://support.ascendcloud.com/"
ARTICLE = "https://support.ascendcloud.com/app/articles/detail/a_id/16781"


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _page(url: str, words: int = 400) -> CrawlResult:
    md = "Real article prose. " * words
    return CrawlResult(
        url=url,
        fit_markdown=md,
        raw_markdown=md,
        html="<html></html>",
        word_count=words,
        success=True,
    )


async def _run(crawl_site_mock: AsyncMock, **kwargs) -> None:
    with (
        patch("knowledge_ingest.adapters.crawler.crawl_site", new=crawl_site_mock),
        patch(
            "knowledge_ingest.adapters.crawler.pg_store.get_crawled_page_hashes",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "knowledge_ingest.adapters.crawler._build_link_graph",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "knowledge_ingest.adapters.crawler._ingest_crawl_result",
            new=AsyncMock(return_value=None),
        ),
    ):
        await run_crawl_job(
            _mock_conn(),
            job_id="job-1",
            org_id="org",
            kb_slug="support",
            start_url=HUB,
            **kwargs,
        )


@pytest.mark.asyncio
async def test_empty_primary_falls_back_to_discovery_seed() -> None:
    """Hub yields nothing → retry from the article, which yields pages."""
    crawl_site = AsyncMock(
        side_effect=[
            ([], []),  # primary crawl of the hub: no ingestable pages
            ([_page(ARTICLE), _page(ARTICLE + "/2")], []),  # from the seed: content
        ]
    )
    await _run(crawl_site, discovery_seed_url=ARTICLE)

    assert crawl_site.await_count == 2
    assert crawl_site.await_args_list[0].kwargs["start_url"] == HUB
    assert crawl_site.await_args_list[1].kwargs["start_url"] == ARTICLE


@pytest.mark.asyncio
async def test_primary_that_reached_the_seed_skips_the_seed_pass() -> None:
    """A healthy site whose BFS reached the seed page pays for one crawl only."""
    crawl_site = AsyncMock(return_value=([_page(HUB + "a"), _page(ARTICLE)], []))
    await _run(crawl_site, discovery_seed_url=ARTICLE)
    assert crawl_site.await_count == 1


@pytest.mark.asyncio
async def test_near_dead_primary_still_crawls_from_the_seed_and_merges() -> None:
    """THE Ascend case (2026-08-13): the client-rendered shell yields a couple
    of junk pages (`/` and `/app/main`), so the primary crawl is NOT empty —
    but none of its links resolve and the seed page is never reached. The old
    `not results` trigger declared victory on those 2 shell pages and ignored
    the operator's validated seed entirely. The trigger is "primary did not
    reach the seed page", and the seed pass MERGES (shell pages kept, seed
    discoveries added, dedup by canonical URL)."""
    ingested: list[str] = []
    crawl_site = AsyncMock(
        side_effect=[
            # Primary: the two junk shell pages — non-empty, seed not reached.
            ([_page(HUB), _page(HUB + "app/main")], []),
            # Seed pass: the article + a BFS discovery + one duplicate shell page.
            ([_page(ARTICLE), _page(ARTICLE + "/2"), _page(HUB)], []),
        ]
    )
    with (
        patch("knowledge_ingest.adapters.crawler.crawl_site", new=crawl_site),
        patch(
            "knowledge_ingest.adapters.crawler.pg_store.get_crawled_page_hashes",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "knowledge_ingest.adapters.crawler._build_link_graph",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "knowledge_ingest.adapters.crawler._ingest_crawl_result",
            new=AsyncMock(side_effect=lambda *a, **kw: ingested.append(a[1].url)),
        ),
    ):
        await run_crawl_job(
            _mock_conn(),
            job_id="job-1",
            org_id="org",
            kb_slug="support",
            start_url=HUB,
            discovery_seed_url=ARTICLE,
        )

    assert crawl_site.await_count == 2
    assert crawl_site.await_args_list[1].kwargs["start_url"] == ARTICLE
    # Merged: shell pages first, seed discoveries appended, duplicate dropped.
    assert ingested == [HUB, HUB + "app/main", ARTICLE, ARTICLE + "/2"]


@pytest.mark.asyncio
async def test_no_discovery_seed_means_no_fallback() -> None:
    """Empty primary + no seed = today's behaviour (single empty crawl)."""
    crawl_site = AsyncMock(return_value=([], []))
    await _run(crawl_site)  # discovery_seed_url defaults to None
    assert crawl_site.await_count == 1


@pytest.mark.asyncio
async def test_seed_equal_to_start_url_is_not_re_crawled() -> None:
    """If the seed canonicalises to the start URL, retrying it would just
    repeat the same empty crawl — skip it."""
    crawl_site = AsyncMock(return_value=([], []))
    # HUB with and without trailing slash canonicalise the same.
    await _run(crawl_site, discovery_seed_url="https://support.ascendcloud.com")
    assert crawl_site.await_count == 1
