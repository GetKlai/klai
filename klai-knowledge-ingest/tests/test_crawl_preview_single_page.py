from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from knowledge_ingest.crawl4ai_client import CrawlResult


def _result() -> CrawlResult:
    return CrawlResult(
        url="https://example.com/page",
        fit_markdown="# Example\n\nSmall page content.",
        raw_markdown="# Example\n\nSmall page content.",
        html="<h1>Example</h1>",
        word_count=4,
        success=True,
        metadata={"status_code": 200},
        response_headers={},
    )


async def test_single_page_crawl_uses_the_interactive_budgets() -> None:
    from knowledge_ingest.crawl4ai_client import crawl_single_page_source

    request = AsyncMock(return_value=_result())
    with patch("knowledge_ingest.crawl4ai_client._crawl_page_with_config", request):
        await crawl_single_page_source("https://example.com/page")

    assert request.await_args.args[1] == {
        "cache_mode": "bypass",
        "wait_until": "domcontentloaded",
        "page_timeout": 20_000,
        "body_visibility_timeout": 2_000,
        "markdown_generator": {
            "type": "DefaultMarkdownGenerator",
            "params": {
                "options": {"type": "dict", "value": {"ignore_links": False, "body_width": 0}},
            },
        },
    }
    assert request.await_args.kwargs == {
        "cookies": None,
        "selector": None,
        "timeout": 60.0,
    }


def test_single_page_source_keeps_tenant_auth_but_bypasses_connector_flow(
    client: TestClient,
) -> None:
    identity = AsyncMock()
    single_page = AsyncMock(return_value=_result())
    stored_selector = AsyncMock()
    connector_crawl = AsyncMock()
    site_sample = AsyncMock()
    validate_url = AsyncMock()

    with (
        patch("knowledge_ingest.routes.crawl.assert_caller_identity_tenant_only", identity),
        patch("knowledge_ingest.routes.crawl.crawl_single_page_source", single_page),
        patch("knowledge_ingest.routes.crawl.get_domain_selector", stored_selector),
        patch("knowledge_ingest.routes.crawl.crawl_page", connector_crawl),
        patch("knowledge_ingest.routes.crawl.sample_linked_pages", site_sample),
        patch("knowledge_ingest.routes.crawl.validate_url_pinned", validate_url),
    ):
        response = client.post(
            "/ingest/v1/crawl/preview",
            json={
                "url": "https://example.com/page",
                "org_id": "zitadel-org-123",
                "single_page_source": True,
            },
        )

    assert response.status_code == 200
    assert response.json()["mode"] == "single_page_source"
    assert response.json()["fit_markdown"] == "# Example\n\nSmall page content."
    assert response.json()["classification"] == "success"
    assert identity.await_count == 1
    assert identity.await_args.kwargs == {"claimed_org_id": "zitadel-org-123"}
    validate_url.assert_awaited_once_with("https://example.com/page", allow_http=True)
    single_page.assert_awaited_once_with("https://example.com/page")
    stored_selector.assert_not_awaited()
    connector_crawl.assert_not_awaited()
    site_sample.assert_not_awaited()


def test_default_preview_still_uses_connector_flow(client: TestClient) -> None:
    connector_crawl = AsyncMock(return_value=_result())
    single_page = AsyncMock()
    validate_url = AsyncMock()

    with (
        patch("knowledge_ingest.routes.crawl.crawl_page", connector_crawl),
        patch("knowledge_ingest.routes.crawl.crawl_single_page_source", single_page),
        patch("knowledge_ingest.routes.crawl.validate_url_pinned", validate_url),
    ):
        response = client.post("/ingest/v1/crawl/preview", json={"url": "https://example.com/page"})

    assert response.status_code == 200
    assert response.json()["mode"] == "connector_preview"
    validate_url.assert_awaited_once_with("https://example.com/page", allow_http=False)
    connector_crawl.assert_awaited_once()
    single_page.assert_not_awaited()
