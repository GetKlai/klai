"""Deel A — a website crawl must not treat document files as pages.

crawl4ai's browser tries to *navigate* to every discovered same-domain link.
Pointed at a PDF (or any other document/archive/media/binary), navigation
starts a download instead of rendering, ``Page.goto`` raises, and crawl4ai
fails the WHOLE bulk chunk with an opaque HTTP 500 — poisoning every
innocent HTML URL sharing that chunk (see
``knowledge_ingest.crawl4ai_client._is_recoverable_bulk_failure`` docstring
for the intermedia.com evidence this fix responds to).

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
    bulk /crawl request — the exact navigation-failure trigger from the
    intermedia.com incident."""

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


def test_a_skipped_pdf_does_not_mark_the_crawl_failed_partial() -> None:
    """The valkuil: a URL we deliberately never wanted to crawl is not
    'incomplete coverage'. Since the excluded URL never produces an
    outcome at all (see the ledger-level tests above), the existing
    not_fetched_* / failure-ratio guards downstream must see a perfectly
    clean, fully-fetched crawl."""
    fetch_outcomes = [
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
