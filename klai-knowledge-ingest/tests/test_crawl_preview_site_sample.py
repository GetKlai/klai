"""Preview must judge the SITE, not just the seed page.

Regression context (2026-08-06, support.ascendcloud.com):

A user adds a helpdesk whose entry URL is an SPA navigation hub. After JS
rendering the hub yields ~92 visible words — eight short of
``_MIN_WORD_COUNT`` — while its ~20 outgoing links each lead to articles of
900-1600 words. The single-page classifier returned
``selector_returns_empty`` and the wizard refused to save the connector,
even though ``crawl_site()`` indexes such a site without trouble (see
``test_crawl_site_frontier_fetches_listing_children``, which follows links
from a seed of word_count=1).

The seed URL a user types is almost always a homepage or hub — precisely
the page type that is navigation by nature. Judging the whole site by that
one page fails hardest on the most common input.

These tests pin the contract: when the seed is thin but a shallow sample of
the site yields usable pages, the preview reports ``success`` and explains
that the entry page itself is navigation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from knowledge_ingest.crawl4ai_client import CrawlResult

# The hub: rich raw HTML (so it is not classified as an SPA shell), thin
# rendered text. Mirrors the measured ascendcloud /app/main page.
_HUB_HTML = "<html>" + ("<div>nav</div>" * 1000) + "</html>"


def _page(url: str, *, words: int, text: str | None = None) -> CrawlResult:
    md = text if text is not None else ("Real article prose about the product. " * (words // 6))
    return CrawlResult(
        url=url,
        fit_markdown=md,
        raw_markdown=md,
        html=_HUB_HTML,
        word_count=words,
        success=True,
        metadata={"status_code": 200},
        response_headers={},
    )


def _hub() -> CrawlResult:
    return _page(
        "https://example.com/app/main",
        words=92,
        text="Welcome to the Support Center. Get support by product.",
    )


def test_thin_hub_seed_is_success_when_site_sample_has_usable_pages(
    client: TestClient,
) -> None:
    """The Ascend case: hub seed below the word threshold, articles behind it.

    Before the site-sample fallback this returned ``selector_returns_empty``
    and the wizard blocked the save.
    """
    sample = [
        _page("https://example.com/app/articles/detail/a_id/16781", words=900),
        _page("https://example.com/app/articles/detail/a_id/15937", words=1370),
        _page("https://example.com/app/articles/detail/a_id/16048", words=1532),
    ]
    with (
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(return_value=_hub()),
        ),
        patch(
            "knowledge_ingest.routes.crawl.crawl_site",
            new=AsyncMock(return_value=(sample, [])),
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/app/main"},
        )
    body = resp.json()
    assert body["classification"] == "success", body
    assert body["sample_pages_usable"] == 3
    # The reason must explain the entry page is navigation — the user needs to
    # understand why the preview text looks empty while the verdict is green.
    assert body["classification_reason"]
    assert "3" in body["classification_reason"]


def test_thin_seed_stays_blocked_when_site_sample_is_also_thin(
    client: TestClient,
) -> None:
    """A genuinely empty site must still surface the actionable classification."""
    sample = [_page("https://example.com/a", words=10)]
    with (
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(return_value=_hub()),
        ),
        patch(
            "knowledge_ingest.routes.crawl.crawl_site",
            new=AsyncMock(return_value=(sample, [])),
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/app/main"},
        )
    body = resp.json()
    assert body["classification"] == "selector_returns_empty", body
    assert body["sample_pages_usable"] == 0


def test_auth_wall_seed_never_triggers_site_sample(client: TestClient) -> None:
    """An auth wall is a real blocker — sampling would waste crawls and could
    mask the fact that the user must supply cookies."""
    walled = CrawlResult(
        url="https://example.com/app/main",
        fit_markdown="Please log in to view this content.",
        raw_markdown="Please log in to view this content.",
        html="<html><input type='password'/></html>",
        word_count=7,
        success=True,
        metadata={"status_code": 401},
        response_headers={},
    )
    sample_mock = AsyncMock(return_value=([], []))
    with (
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(return_value=walled),
        ),
        patch("knowledge_ingest.routes.crawl.crawl_site", new=sample_mock),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/app/main"},
        )
    body = resp.json()
    assert body["classification"] == "auth_wall_detected", body
    sample_mock.assert_not_awaited()


def test_healthy_seed_never_triggers_site_sample(client: TestClient) -> None:
    """A seed that already passes must not pay for an extra crawl."""
    md = "Real article with proper prose. " * 60
    sample_mock = AsyncMock(return_value=([], []))
    with (
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(return_value=_page("https://example.com/a", words=600)),
        ),
        patch("knowledge_ingest.routes.crawl.crawl_site", new=sample_mock),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/a", "content_selector": "article"},
        )
    assert resp.json()["classification"] == "success"
    assert md  # keep the fixture intent explicit
    sample_mock.assert_not_awaited()


def test_site_sample_timeout_falls_back_to_single_page_verdict(
    client: TestClient,
) -> None:
    """A slow site must not turn a working preview into "did not respond".

    portal-api caps the whole preview call, and the seed crawl has already
    spent part of that budget. If the sample ran unbounded it would push slow
    sites past that ceiling — replacing an actionable thin-content verdict
    with a generic service error, which is strictly worse than before.
    """
    with (
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(return_value=_hub()),
        ),
        patch(
            "knowledge_ingest.routes.crawl.crawl_site",
            new=AsyncMock(side_effect=TimeoutError()),
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/app/main"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "selector_returns_empty", body
    assert body["sample_pages_crawled"] == 0


def test_site_sample_failure_falls_back_to_single_page_verdict(
    client: TestClient,
) -> None:
    """A crashing sample crawl must not turn the preview into a 500."""
    with (
        patch(
            "knowledge_ingest.routes.crawl.crawl_page",
            new=AsyncMock(return_value=_hub()),
        ),
        patch(
            "knowledge_ingest.routes.crawl.crawl_site",
            new=AsyncMock(side_effect=RuntimeError("crawl4ai down")),
        ),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/app/main"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "selector_returns_empty", body
