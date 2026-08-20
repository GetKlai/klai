"""A website crawl must not chase un-rendered client-side template syntax.

support.ascendcloud.com's search-results widget emits an ``<a href>``
whose template placeholder never got interpolated (the templating
framework's JS never ran, or crawl4ai captured the DOM before it mounted):

    https://support.ascendcloud.com/euf/generated/optimized/1782421674/
    themes/standard/{{item.SearchResultURL != '' ?+item.SearchResultURL+...}}

That is never a real page. Fetching it 404s with a tiny error body, and
crawl4ai's structural anti-bot heuristic (``_classify_fetch_outcome``)
misreads the small page as a block. At the time, BLOCKED_ANTI_BOT
unconditionally stopped chunking, so four template-syntax URLs cost 192
real pages that were never attempted (production incident, 2026-08-18).
Anti-bot stops now use a crawl-wide ratio+floor gate, but these bogus URLs
still must not consume requests or distort that ratio.

These URLs are filtered at the frontier (``CrawlLedger.add``), before a
single network request is made — same mechanism and same contract as
``_url_has_non_html_extension`` (test_crawl_frontier_document_extensions.py):
excluded URLs must never produce a ``not_fetched_*`` outcome, or a single
template-syntax link anywhere on a site would mark an otherwise-perfect
crawl ``failed_partial`` (``_build_crawl_outcome_warning`` /
``_crawl_fully_fetched`` in ``knowledge_ingest/adapters/crawler.py`` both
key off any ``not_fetched_*``-prefixed reason code).
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

# The exact production URL (2026-08-18, support.ascendcloud.com).
_PRODUCTION_TEMPLATE_URL = (
    "https://support.ascendcloud.com/euf/generated/optimized/1782421674/"
    "themes/standard/{{item.SearchResultURL != '' ?+item.SearchResultURL+...}}"
)

# The same URL, percent-encoded the way a site's JS could emit it if its
# templating layer ran the (un-interpolated) placeholder through
# encodeURIComponent before writing it into the href attribute.
_PRODUCTION_TEMPLATE_URL_PERCENT_ENCODED = (
    "https://support.ascendcloud.com/euf/generated/optimized/1782421674/"
    "themes/standard/%7B%7Bitem.SearchResultURL%20!%3D%20''%20%3F%2Bitem."
    "SearchResultURL%2B...%7D%7D"
)


def _ledger(**overrides: Any) -> CrawlLedger:
    defaults: dict[str, Any] = {
        "start_url": "https://support.ascendcloud.com",
        "base_domain": "support.ascendcloud.com",
        "include_patterns": None,
        "exclude_patterns": None,
        "max_depth": 3,
    }
    defaults.update(overrides)
    return CrawlLedger(**defaults)


@pytest.mark.parametrize(
    "url",
    [
        _PRODUCTION_TEMPLATE_URL,
        _PRODUCTION_TEMPLATE_URL_PERCENT_ENCODED,
    ],
)
def test_production_template_placeholder_url_is_never_added_to_the_frontier(url: str) -> None:
    ledger = _ledger()
    added = ledger.add(
        url,
        depth=1,
        discovered_from="https://support.ascendcloud.com",
        source_kind="page_link",
        priority=50,
    )
    assert added is False, f"expected {url} to be excluded from the frontier"
    assert ledger.discovered_count == 0


@pytest.mark.parametrize(
    "url",
    [
        # Handlebars/Angular/Vue
        "https://example.com/products/{{product.slug}}",
        # JavaScript template literal
        "https://example.com/user/${userId}/profile",
        # ERB / JSP / ASP
        "https://example.com/page.jsp?id=<%= id %>",
        # Jinja / Django / Liquid
        "https://example.com/{% if user %}dashboard{% endif %}",
        # percent-encoded Handlebars, lowercase hex digits
        "https://example.com/products/%7b%7bproduct.slug%7d%7d",
    ],
)
def test_generic_template_placeholder_patterns_are_never_added_to_the_frontier(
    url: str,
) -> None:
    ledger = CrawlLedger(
        start_url="https://example.com",
        base_domain="example.com",
        include_patterns=None,
        exclude_patterns=None,
        max_depth=3,
    )
    added = ledger.add(
        url,
        depth=1,
        discovered_from="https://example.com",
        source_kind="page_link",
        priority=50,
    )
    assert added is False, f"expected {url} to be excluded from the frontier"
    assert ledger.discovered_count == 0


def test_real_url_next_to_a_template_url_is_still_added() -> None:
    ledger = _ledger()
    ledger.add(
        _PRODUCTION_TEMPLATE_URL,
        depth=1,
        discovered_from="https://support.ascendcloud.com",
        source_kind="page_link",
        priority=50,
    )
    added = ledger.add(
        "https://support.ascendcloud.com/euf/assets/1782421674/Support",
        depth=1,
        discovered_from="https://support.ascendcloud.com",
        source_kind="page_link",
        priority=50,
    )
    assert added is True
    assert ledger.discovered_count == 1


def test_a_crawl_with_only_skipped_template_urls_is_not_failed_partial() -> None:
    """The valkuil: a URL we deliberately never wanted to crawl is not
    'incomplete coverage'. A filtered start URL produces an explicit outcome,
    so downstream coverage guards must ignore that deliberate exclusion — mirrors
    test_a_skipped_pdf_does_not_mark_the_crawl_failed_partial in
    test_crawl_frontier_document_extensions.py."""
    fetch_outcomes = [
        {
            "url": "https://support.ascendcloud.com/{{article.url}}",
            "reason_code": "not_fetched_excluded",
            "status_code": None,
            "content_length": 0,
            "filter_reason": "unrendered_template_syntax",
        },
        {
            "url": "https://support.ascendcloud.com",
            "reason_code": "success",
            "status_code": 200,
            "content_length": 500,
        },
        {
            "url": "https://support.ascendcloud.com/euf/assets/1782421674/Support",
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


@pytest.mark.asyncio
async def test_template_start_url_is_reported_without_being_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[str] = []

    async def _fake_seed(*, start_url: str, **_kwargs: Any) -> CrawlResult:
        fetched.append(start_url)
        return CrawlResult(
            url=start_url,
            fit_markdown="Template content",
            raw_markdown="Template content",
            html="<html></html>",
            word_count=2,
            success=True,
        )

    async def _no_sitemap(_base: str) -> list[str]:
        return []

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)
    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _no_sitemap)

    results, outcomes = await crawl4ai_client.crawl_site(
        start_url=_PRODUCTION_TEMPLATE_URL,
    )

    assert fetched == []
    assert results == []
    assert outcomes[0]["url"] == _PRODUCTION_TEMPLATE_URL
    assert outcomes[0]["reason_code"] == FetchReasonCode.NOT_FETCHED_EXCLUDED.value
    assert outcomes[0]["filter_reason"] == "unrendered_template_syntax"
