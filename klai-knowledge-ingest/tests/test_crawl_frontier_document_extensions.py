"""A website crawl must not treat document files as pages.

crawl4ai's browser tries to *navigate* to every discovered same-domain
link. Pointed at a PDF (or any other document/archive/media/binary),
navigation starts a download instead of rendering, and the request fails.

This is NOT about one bad URL poisoning a whole bulk batch — measured
directly against the running crawl4ai container (2026-08-18): a PDF
submitted together with a good URL in one bulk request comes back HTTP
200 with two results, the PDF's own ``success: false`` and an error
message, the good page fetched normally. crawl4ai's bulk endpoint
(``crawler.arun_many``) isolates per-URL failures correctly.

The real cost is downstream: a PDF that reaches the crawl frontier can
end up in Klai's own sequential-recovery route
(``knowledge_ingest.crawl4ai_client._recover_bulk_5xx_batch``) or the
seed fetch (``_fetch_seed_page``) — both single-URL requests, which
crawl4ai dispatches to ``crawler.arun`` instead of ``arun_many``, and
which does NOT isolate the failure the same way. A PDF can never become
fetchable HTML no matter how many times or how slowly it is retried, so
letting one reach that route burns a real 75-second cooldown
(``crawl_sequential_recovery_cooldown_seconds``) and a
``_MAX_SEQUENTIAL_RECOVERY`` budget slot that a genuinely recoverable
HTML page needed instead.

These URLs are filtered at the frontier (``CrawlLedger.add``), before a
single network request is made — same mechanism as ``exclude_patterns``,
deliberately: excluded-by-extension URLs must never produce a
``not_fetched_*`` outcome, or a single PDF link anywhere on a site would
mark an otherwise-perfect crawl ``failed_partial``
(``_build_crawl_outcome_warning`` / ``_crawl_fully_fetched`` in
``knowledge_ingest/adapters/crawler.py`` both key off any
``not_fetched_*``-prefixed reason code).
"""

from __future__ import annotations

from typing import Any

import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.adapters.crawler import (
    _build_crawl_outcome_warning,
    _crawl_fully_fetched,
    decide_fetch_failure_terminal_status,
)
from knowledge_ingest.crawl4ai_client import CrawlLedger, CrawlResult
from knowledge_ingest.reason_codes import FetchReasonCode


def _ledger(**overrides: Any) -> CrawlLedger:
    defaults: dict[str, Any] = {
        "start_url": "https://example.com",
        "base_domain": "example.com",
        "include_patterns": None,
        "exclude_patterns": None,
        "max_depth": 3,
    }
    defaults.update(overrides)
    return CrawlLedger(**defaults)


def test_pdf_link_is_never_added_to_the_frontier() -> None:
    ledger = _ledger()
    added = ledger.add(
        "https://example.com/assets/pdf/report.pdf",
        depth=1,
        discovered_from="https://example.com",
        source_kind="page_link",
        priority=50,
    )
    assert added is False
    assert ledger.discovered_count == 0


def test_html_link_next_to_a_pdf_link_is_still_added() -> None:
    ledger = _ledger()
    ledger.add(
        "https://example.com/assets/pdf/report.pdf",
        depth=1,
        discovered_from="https://example.com",
        source_kind="page_link",
        priority=50,
    )
    added = ledger.add(
        "https://example.com/products/widget",
        depth=1,
        discovered_from="https://example.com",
        source_kind="page_link",
        priority=50,
    )
    assert added is True
    assert ledger.discovered_count == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/assets/pdf/report.pdf?download=1",
        "https://example.com/whitepaper.PDF?utm_source=newsletter",
        "https://example.com/downloads/manual.docx",
        "https://example.com/archive/backup.zip",
        "https://example.com/images/logo.png",
        "https://example.com/media/demo.mp4",
        "https://example.com/installers/setup.exe",
    ],
)
def test_non_html_extensions_are_excluded_including_with_query_params(url: str) -> None:
    ledger = _ledger()
    added = ledger.add(
        url,
        depth=1,
        discovered_from="https://example.com",
        source_kind="page_link",
        priority=50,
    )
    assert added is False, f"expected {url} to be excluded from the frontier"
    assert ledger.discovered_count == 0


def test_path_segment_containing_a_dot_but_no_recognised_extension_is_kept() -> None:
    """A path with a dot that is not a document/archive/media/binary extension
    (e.g. a versioned doc path) must not be swept up by an overly broad filter."""
    ledger = _ledger()
    added = ledger.add(
        "https://example.com/docs/v1.2/getting-started",
        depth=1,
        discovered_from="https://example.com",
        source_kind="page_link",
        priority=50,
    )
    assert added is True


def test_add_links_from_result_skips_pdf_hrefs() -> None:
    ledger = _ledger()
    result = CrawlResult(
        url="https://example.com/support",
        fit_markdown="Support",
        raw_markdown="Support",
        html="<html></html>",
        word_count=2,
        success=True,
        links={
            "internal": [
                {"href": "https://example.com/assets/pdf/report.pdf", "text": "Report"},
                {"href": "https://example.com/support/faq", "text": "FAQ"},
            ]
        },
    )
    ledger.add_links_from_result(result, source_depth=1)
    assert ledger.discovered_count == 1
    assert ledger.next_batch(remaining_budget=10) == ["https://example.com/support/faq"]


@pytest.mark.asyncio
async def test_crawl_site_never_submits_pdf_links_to_crawl4ai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a PDF discovered on the seed page must never reach the
    bulk /crawl request, or (more importantly) the single-URL sequential
    recovery route it would otherwise be retried through."""

    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=start_url,
            fit_markdown="Seed",
            raw_markdown="Seed",
            html="<html></html>",
            word_count=2,
            success=True,
            links={
                "internal": [
                    {"href": "https://example.com/assets/pdf/report.pdf", "text": "Report"},
                    {"href": "https://example.com/products/widget", "text": "Widget"},
                ]
            },
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)

    async def _no_sitemap(_base: str) -> list[str]:
        return []

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _no_sitemap)

    submitted: list[list[str]] = []

    async def _fake_crawl_sync(_client: Any, payload: dict[str, Any]) -> dict[str, Any]:
        urls = payload["urls"]
        submitted.append(urls)
        return {
            "results": [
                {
                    "url": u,
                    "success": True,
                    "status_code": 200,
                    "html": "<html><body>Real content, several words here.</body></html>",
                    "markdown": "Real content, several words here.",
                    "links": {"internal": []},
                    "media": {},
                }
                for u in urls
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
    )

    for batch in submitted:
        assert "https://example.com/assets/pdf/report.pdf" not in batch

    urls_in_outcomes = {o["url"] for o in outcomes}
    assert "https://example.com/assets/pdf/report.pdf" not in urls_in_outcomes
    assert "https://example.com/products/widget" in urls_in_outcomes
    assert any(r.url == "https://example.com/products/widget" for r in results)


@pytest.mark.asyncio
async def test_pdf_start_url_is_reported_without_being_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start_url = "https://example.com/manual.pdf"
    fetched: list[str] = []

    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> CrawlResult:
        fetched.append(start_url)
        return CrawlResult(
            url=start_url,
            fit_markdown="PDF content",
            raw_markdown="PDF content",
            html="<html></html>",
            word_count=2,
            success=True,
        )

    async def _no_sitemap(_base: str) -> list[str]:
        return []

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)
    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _no_sitemap)

    results, outcomes = await crawl4ai_client.crawl_site(start_url=start_url)

    assert fetched == []
    assert results == []
    assert outcomes[0]["url"] == start_url
    assert outcomes[0]["reason_code"] == FetchReasonCode.NOT_FETCHED_EXCLUDED.value
    assert outcomes[0]["filter_reason"] == "non_html_extension"


def test_a_skipped_pdf_does_not_mark_the_crawl_failed_partial() -> None:
    """The valkuil: a URL we deliberately never wanted to crawl is not
    'incomplete coverage'. A filtered start URL does produce an explicit
    outcome, so downstream coverage guards must distinguish that deliberate
    exclusion from an unfinished frontier."""
    fetch_outcomes = [
        {
            "url": "https://example.com/manual.pdf",
            "reason_code": "not_fetched_excluded",
            "status_code": None,
            "content_length": 0,
            "filter_reason": "non_html_extension",
        },
        {
            "url": "https://example.com",
            "reason_code": "success",
            "status_code": 200,
            "content_length": 500,
        },
        {
            "url": "https://example.com/products/widget",
            "reason_code": "success",
            "status_code": 200,
            "content_length": 500,
        },
    ]

    assert _build_crawl_outcome_warning(fetch_outcomes, max_pages=200) is None
    assert _crawl_fully_fetched(fetch_outcomes) is True
    status, summary = decide_fetch_failure_terminal_status(
        fetch_outcomes=fetch_outcomes,
        threshold=0.30,
    )
    assert status == ""
    assert summary is None


def test_a_filtered_seed_without_any_success_still_fails_loudly() -> None:
    warning = _build_crawl_outcome_warning(
        [
            {
                "url": "https://example.com/manual.pdf",
                "reason_code": "not_fetched_excluded",
                "status_code": None,
                "content_length": 0,
                "filter_reason": "non_html_extension",
            }
        ],
        max_pages=200,
    )

    assert warning is not None
    assert warning["reason"] == "crawl_fetch_failed"
