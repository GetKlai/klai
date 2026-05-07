"""Tests for ``CrawlPreviewResponse.classification`` (REQ-3).

SPEC-CONNECTOR-INPUT-VALIDATION-001 / REQ-3 / REQ-6.

The preview endpoint returns a ``classification`` field that the wizard
uses to decide whether to enable the "Add connector" button at step 6.
Five outcomes:

- ``success``
- ``selector_required`` (link-density too high)
- ``selector_returns_empty`` (word_count too low, raw HTML > 5KB)
- ``requires_javascript`` (word_count low, raw HTML < 5KB — SPA)
- ``auth_wall_detected`` (REQ-2 heuristic matched)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from knowledge_ingest.crawl4ai_client import CrawlResult


def _result(
    *,
    fit_markdown: str,
    raw_markdown: str = "",
    html: str = "",
    word_count: int = 0,
    metadata: dict | None = None,
    response_headers: dict[str, str] | None = None,
) -> CrawlResult:
    return CrawlResult(
        url="https://example.com/page",
        fit_markdown=fit_markdown,
        raw_markdown=raw_markdown or fit_markdown,
        html=html,
        word_count=word_count,
        success=True,
        metadata=metadata or {"status_code": 200},
        response_headers=response_headers or {},
    )


def test_preview_classification_success_for_clean_article(client: TestClient) -> None:
    md = "Real article with proper prose. " * 60
    healthy = _result(fit_markdown=md, raw_markdown=md, word_count=600)
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=healthy),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/page", "content_selector": "article"},
        )
    body = resp.json()
    assert body["classification"] == "success"


def test_preview_classification_selector_required_for_nav_dominated(
    client: TestClient,
) -> None:
    """Word count is high enough but link-density crosses 40% — wizard MUST
    redirect the user back to step 5 to refine the selector."""
    nav_md = (
        "[Home Page](/h) [About Us](/a) [Contact Information](/c) "
        "[Our Products](/p) [Login Now](/l) [Signup Free](/s) "
        "[Documentation](/d) [Help Center](/help) [Pricing](/pricing) "
        "[Support](/support) [Knowledge Base](/kb) [Resources](/r) "
        "[Blog Articles](/blog) [Latest News](/news) [Career Jobs](/jobs)"
    ) * 5
    nav_dominated = _result(
        fit_markdown=nav_md,
        raw_markdown=nav_md,
        word_count=200,  # high enough word count, but mostly link text
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=nav_dominated),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/page"},
        )
    body = resp.json()
    assert body["classification"] == "selector_required"


def test_preview_classification_selector_returns_empty_with_thin_md_rich_html(
    client: TestClient,
) -> None:
    thin = _result(
        fit_markdown="Just a tiny stub.",
        raw_markdown="Just a tiny stub.",
        html="<html>" + ("<div>x</div>" * 1000) + "</html>",
        word_count=4,
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=thin),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={
                "url": "https://example.com/page",
                "content_selector": ".never-matches",
            },
        )
    body = resp.json()
    assert body["classification"] == "selector_returns_empty"


def test_preview_classification_requires_javascript_when_html_minimal(
    client: TestClient,
) -> None:
    spa = _result(
        fit_markdown="",
        raw_markdown="",
        html="<html><div id='root'></div></html>",  # < 5KB
        word_count=0,
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=spa),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/spa"},
        )
    body = resp.json()
    assert body["classification"] == "requires_javascript"


def test_preview_classification_auth_wall_detected_redirects_to_step_4(
    client: TestClient,
) -> None:
    """Page has enough words but the auth-wall heuristic fires anyway —
    classifier MUST surface ``auth_wall_detected`` so the wizard sends the
    user back to step 4 (auth setup), not step 5."""
    walled = _result(
        fit_markdown="A short teaser. " * 5 + "Inloggen om verder te lezen",
        raw_markdown="A short teaser. " * 5 + "Inloggen om verder te lezen",
        word_count=80,
        metadata={"status_code": 200},
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=walled),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/walled"},
        )
    body = resp.json()
    assert body["classification"] == "auth_wall_detected"


def test_preview_classification_auth_wall_detected_when_walled_with_high_word_count(
    client: TestClient,
) -> None:
    """Edge: even with high word_count, if the auth-wall classifier fires
    (e.g., HTTP 401 + body present), the classification MUST be auth_wall_detected,
    NOT success — auth wins over selector check."""
    walled_with_words = _result(
        fit_markdown="Some content. " * 60,
        raw_markdown="Some content. " * 60,
        word_count=600,
        metadata={"status_code": 401},  # 401 trips http_unauthenticated
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=walled_with_words),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview",
            json={"url": "https://example.com/walled-with-words"},
        )
    body = resp.json()
    assert body["classification"] == "auth_wall_detected"


def test_preview_classification_field_always_present(client: TestClient) -> None:
    """Even on the failure path the classification field must exist
    (UI relies on it for branching)."""
    healthy = _result(
        fit_markdown="Real article. " * 60, raw_markdown="Real article. " * 60, word_count=600
    )
    with patch(
        "knowledge_ingest.routes.crawl.crawl_page",
        new=AsyncMock(return_value=healthy),
    ):
        resp = client.post(
            "/ingest/v1/crawl/preview", json={"url": "https://example.com/page"}
        )
    body = resp.json()
    assert "classification" in body
    assert body["classification"] in {
        "success",
        "selector_required",
        "selector_returns_empty",
        "requires_javascript",
        "auth_wall_detected",
        "unknown",  # fail-closed default for upstream errors / unclassified paths
    }
