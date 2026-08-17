"""Per-domain adaptive rate-limit lowering (2026-08-17 incident).

A domain that returns RATE_LIMITED or BLOCKED_ANTI_BOT outcomes should not
get hammered again at the same pace on the next crawl. ``run_crawl_job``
stores a halved rate_limit in ``knowledge.crawl_domains`` for that domain
(``domain_selectors.lower_domain_rate_limit``) and applies a previously
stored value on the NEXT crawl instead of the caller's default
(``domain_selectors.get_domain_rate_limit``).

These tests mock ``crawl_site`` entirely — the crawl4ai wiring itself is
covered by tests/test_build_crawl_config.py and
tests/test_crawl_site_reconcile.py. Here the concern is purely: does
run_crawl_job read the stored override, and does it write one back when the
signal fires.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.adapters.crawler import run_crawl_job
from knowledge_ingest.crawl4ai_client import CrawlResult
from knowledge_ingest.reason_codes import FetchReasonCode

START_URL = "https://intermedia.com/support"
DOMAIN = "intermedia.com"


def _mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _page(url: str) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown="Real content here.",
        raw_markdown="Real content here.",
        html="<html></html>",
        word_count=10,
        success=True,
    )


async def _run(
    *,
    crawl_site_mock: AsyncMock,
    get_domain_rate_limit_mock: AsyncMock,
    lower_domain_rate_limit_mock: AsyncMock,
    rate_limit: float = 2.0,
) -> None:
    with (
        patch("knowledge_ingest.adapters.crawler.crawl_site", new=crawl_site_mock),
        patch(
            "knowledge_ingest.adapters.crawler.get_domain_rate_limit",
            new=get_domain_rate_limit_mock,
        ),
        patch(
            "knowledge_ingest.adapters.crawler.lower_domain_rate_limit",
            new=lower_domain_rate_limit_mock,
        ),
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
            org_id="org-1",
            kb_slug="support",
            start_url=START_URL,
            rate_limit=rate_limit,
        )


@pytest.mark.asyncio
async def test_rate_limited_outcome_lowers_the_domain_rate_limit() -> None:
    """A crawl whose outcomes include RATE_LIMITED must halve and persist
    the rate for this domain (floor 0.2), starting from the rate_limit
    actually used for this run."""
    crawl_site = AsyncMock(
        return_value=(
            [_page(START_URL)],
            [
                {
                    "url": START_URL,
                    "reason_code": FetchReasonCode.SUCCESS.value,
                    "status_code": 200,
                    "content_length": 100,
                },
                {
                    "url": "https://intermedia.com/support/a",
                    "reason_code": FetchReasonCode.RATE_LIMITED.value,
                    "status_code": None,
                    "content_length": 0,
                },
            ],
        )
    )
    get_domain_rate_limit = AsyncMock(return_value=None)  # no prior override
    lower_domain_rate_limit = AsyncMock(return_value=None)

    await _run(
        crawl_site_mock=crawl_site,
        get_domain_rate_limit_mock=get_domain_rate_limit,
        lower_domain_rate_limit_mock=lower_domain_rate_limit,
        rate_limit=2.0,
    )

    lower_domain_rate_limit.assert_awaited_once()
    args = lower_domain_rate_limit.await_args
    assert args.args[1] == DOMAIN
    assert args.args[2] == "org-1"
    assert args.args[3] == pytest.approx(1.0)  # 2.0 / 2


@pytest.mark.asyncio
async def test_blocked_anti_bot_outcome_also_lowers_the_domain_rate_limit() -> None:
    """BLOCKED_ANTI_BOT is the sibling trigger to RATE_LIMITED — a site
    that anti-bot-blocked us should also be paced down next time."""
    crawl_site = AsyncMock(
        return_value=(
            [_page(START_URL)],
            [
                {
                    "url": "https://intermedia.com/support/b",
                    "reason_code": FetchReasonCode.BLOCKED_ANTI_BOT.value,
                    "status_code": None,
                    "content_length": 0,
                },
            ],
        )
    )
    get_domain_rate_limit = AsyncMock(return_value=None)
    lower_domain_rate_limit = AsyncMock(return_value=None)

    await _run(
        crawl_site_mock=crawl_site,
        get_domain_rate_limit_mock=get_domain_rate_limit,
        lower_domain_rate_limit_mock=lower_domain_rate_limit,
        rate_limit=2.0,
    )

    lower_domain_rate_limit.assert_awaited_once()


@pytest.mark.asyncio
async def test_healthy_crawl_does_not_touch_the_domain_rate_limit() -> None:
    """No RATE_LIMITED / BLOCKED_ANTI_BOT signal → nothing written. A
    healthy site is never touched by the adaptive throttle."""
    crawl_site = AsyncMock(
        return_value=(
            [_page(START_URL)],
            [
                {
                    "url": START_URL,
                    "reason_code": FetchReasonCode.SUCCESS.value,
                    "status_code": 200,
                    "content_length": 100,
                },
            ],
        )
    )
    get_domain_rate_limit = AsyncMock(return_value=None)
    lower_domain_rate_limit = AsyncMock(return_value=None)

    await _run(
        crawl_site_mock=crawl_site,
        get_domain_rate_limit_mock=get_domain_rate_limit,
        lower_domain_rate_limit_mock=lower_domain_rate_limit,
    )

    lower_domain_rate_limit.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_previously_lowered_rate_is_applied_on_the_next_crawl() -> None:
    """A stored override for this domain must be used INSTEAD OF the
    caller's default rate_limit, on both crawl_site call sites."""
    crawl_site = AsyncMock(
        return_value=(
            [_page(START_URL)],
            [
                {
                    "url": START_URL,
                    "reason_code": FetchReasonCode.SUCCESS.value,
                    "status_code": 200,
                    "content_length": 100,
                },
            ],
        )
    )
    get_domain_rate_limit = AsyncMock(return_value=0.5)  # stored override
    lower_domain_rate_limit = AsyncMock(return_value=None)

    await _run(
        crawl_site_mock=crawl_site,
        get_domain_rate_limit_mock=get_domain_rate_limit,
        lower_domain_rate_limit_mock=lower_domain_rate_limit,
        rate_limit=2.0,  # caller's default — must be overridden
    )

    crawl_site.assert_awaited_once()
    assert crawl_site.await_args.kwargs["rate_limit"] == 0.5


@pytest.mark.asyncio
async def test_lowering_never_goes_below_the_floor() -> None:
    """Repeated lowering on an already-low stored rate must not asymptote
    toward zero — floor is 0.2 req/s."""
    crawl_site = AsyncMock(
        return_value=(
            [_page(START_URL)],
            [
                {
                    "url": "https://intermedia.com/support/a",
                    "reason_code": FetchReasonCode.RATE_LIMITED.value,
                    "status_code": None,
                    "content_length": 0,
                },
            ],
        )
    )
    get_domain_rate_limit = AsyncMock(return_value=0.3)  # already low
    lower_domain_rate_limit = AsyncMock(return_value=None)

    await _run(
        crawl_site_mock=crawl_site,
        get_domain_rate_limit_mock=get_domain_rate_limit,
        lower_domain_rate_limit_mock=lower_domain_rate_limit,
        rate_limit=2.0,
    )

    lower_domain_rate_limit.assert_awaited_once()
    assert lower_domain_rate_limit.await_args.args[3] == pytest.approx(0.2)  # floor, not 0.15
