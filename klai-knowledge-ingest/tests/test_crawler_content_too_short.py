"""Damage 2 (stop-the-bleeding fix): error pages must not be persisted as knowledge.

Production incident (2026-08-18): openapi.eu-production.holodeck.voys.nl
served 13 pages that all successfully fetched (HTTP 200) and were all
exactly 92 characters of the same OpenAPI-parser error text:

    #### Failed to parse OpenAPI file
    Please make sure your OpenAPI file is valid and try again

There was no minimum-content-length check anywhere on the crawl-ingest
path, so 5 of those 13 pages were persisted into the customer's knowledge
base with plausible-looking titles (``call``, ``callrecording``, ...) and
became eligible for retrieval in chat. The other 8 were only rejected once
the template-cluster detector saw 5 near-identical siblings — a detector
whose whole job is auth-wall detection, not error-page filtering.

``PersistSkipReason.CONTENT_TOO_SHORT`` already existed in
``reason_codes.py`` but was wired up nowhere. This test locks in that the
crawl-ingest path now uses it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.config import settings
from knowledge_ingest.crawl4ai_client import CrawlResult

# Reconstructed from the incident report (exact production string not
# available from this environment — no DB/log access). ~91 chars, matching
# the reported "exactly 92 characters" within reconstruction tolerance.
_OPENAPI_PARSE_ERROR_TEXT = (
    "#### Failed to parse OpenAPI file\nPlease make sure your OpenAPI file is valid and try again"
)


def _make_mock_conn():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.executemany = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


def _make_crawl_result(url: str, text: str) -> CrawlResult:
    return CrawlResult(
        url=url,
        fit_markdown=text,
        raw_markdown=text,
        html=f"<html><body><p>{text}</p></body></html>",
        word_count=len(text.split()),
        success=True,
        links={"internal": []},
        error_message="",
        metadata={},
        response_headers={"content-type": "text/html"},
    )


@pytest.mark.asyncio
async def test_openapi_error_page_is_not_persisted():
    """The ~92-char production regression must be skipped, not ingested."""
    assert len(_OPENAPI_PARSE_ERROR_TEXT) < settings.ingest_min_content_length, (
        "fixture must stay below the configured threshold to exercise the regression"
    )
    mock_conn = _make_mock_conn()
    result = _make_crawl_result(
        "https://openapi.eu-production.holodeck.voys.nl/call",
        _OPENAPI_PARSE_ERROR_TEXT,
    )

    from tests.conftest import make_pg_store_mock

    mock_pg = make_pg_store_mock()
    with (
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            mock_conn,
            result,
            url=result.url,
            org_id="org-1",
            kb_slug="docs",
            stored=None,
        )

    mock_ingest.assert_not_called()
    mock_pg.upsert_crawled_page.assert_not_called()


@pytest.mark.asyncio
async def test_short_legitimate_page_just_above_threshold_is_persisted():
    """A short but real page just above the threshold must still be ingested.

    Guards against an overcorrection that would drop legitimate short pages
    (e.g. the production case of a connector with exactly 1 short document).
    """
    text = "x" * (settings.ingest_min_content_length + 1)
    mock_conn = _make_mock_conn()
    result = _make_crawl_result("https://example.com/short-but-real", text)

    from tests.conftest import make_pg_store_mock

    mock_pg = make_pg_store_mock()
    with (
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
        ) as mock_ingest,
    ):
        mock_ingest.return_value = {"chunks": 1}

        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            mock_conn,
            result,
            url=result.url,
            org_id="org-1",
            kb_slug="docs",
            stored=None,
        )

    mock_ingest.assert_called_once()


@pytest.mark.asyncio
async def test_content_too_short_is_logged_with_reason_code():
    """The skip must be observable, not silent (see rule: silent skips hide for years).

    NOTE: asserts against a mocked ``crawler.logger`` rather than
    ``caplog`` — structlog in this codebase only routes through stdlib
    ``logging`` (which ``caplog`` intercepts) after ``knowledge_ingest.app``
    has been imported at least once in the test session. Whether that has
    happened depends on pytest's collection order across the whole suite,
    so a ``caplog``-based assertion here would be order-dependent. Mocking
    ``crawler.logger`` directly is order-independent.
    """
    mock_conn = _make_mock_conn()
    result = _make_crawl_result("https://example.com/junk", _OPENAPI_PARSE_ERROR_TEXT)

    from tests.conftest import make_pg_store_mock

    mock_pg = make_pg_store_mock()
    mock_logger = MagicMock()
    with (
        patch("knowledge_ingest.adapters.crawler.pg_store", mock_pg),
        patch("knowledge_ingest.adapters.crawler.logger", mock_logger),
        patch(
            "knowledge_ingest.routes.ingest.ingest_document",
            new_callable=AsyncMock,
        ),
    ):
        from knowledge_ingest.adapters.crawler import _ingest_crawl_result

        await _ingest_crawl_result(
            mock_conn,
            result,
            url=result.url,
            org_id="org-1",
            kb_slug="docs",
            stored=None,
        )

    logged_events = [call.args[0] for call in mock_logger.info.call_args_list if call.args]
    assert "crawl_skipped_content_too_short" in logged_events
