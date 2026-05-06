"""SPEC-INGEST-RECONCILE-001 — unit tests for the new crawl_site helpers.

Covers:

- ``_canonicalise_url``: dedup-key normalisation (AC-1, AC-2 dedup column).
- ``_build_candidate_set``: sitemap-priority on cap, include_patterns
  filter, cross-domain filter, dedup (AC-1, AC-2).
- ``_classify_fetch_outcome``: stable mapping of crawl4ai response
  shapes + transport exceptions to FetchReasonCode (AC-4, AC-11).
- ``crawl_site`` bulk-path on a transport failure: every candidate gets
  a non-success outcome (AC-4 — no candidate is "lost").
- ``crawl_site`` returns the new ``(results, outcomes)`` tuple shape
  with one outcome per candidate.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import httpx
import pytest

from knowledge_ingest import crawl4ai_client
from knowledge_ingest.crawl4ai_client import (
    CrawlResult,
    _build_candidate_set,
    _canonicalise_url,
    _classify_fetch_outcome,
)
from knowledge_ingest.reason_codes import FetchReasonCode

# ---------------------------------------------------------------------------
# _canonicalise_url
# ---------------------------------------------------------------------------


class TestCanonicaliseUrl:
    def test_strips_trailing_slash_on_non_root_path(self) -> None:
        assert _canonicalise_url("https://example.com/page/") == "https://example.com/page"

    def test_keeps_root_slash(self) -> None:
        # Empty path becomes "/" by URL contract — we keep it as "/".
        assert _canonicalise_url("https://example.com/").endswith("/")

    def test_strips_fragment(self) -> None:
        assert (
            _canonicalise_url("https://example.com/page#section")
            == "https://example.com/page"
        )

    def test_lowercases_scheme_and_host(self) -> None:
        assert (
            _canonicalise_url("HTTPS://Example.COM/Page")
            == "https://example.com/Page"
        )

    def test_preserves_query(self) -> None:
        # Query string variants are different pages by convention; we keep them.
        assert (
            _canonicalise_url("https://example.com/page?foo=bar")
            == "https://example.com/page?foo=bar"
        )

    def test_dedups_trailing_slash_against_no_slash(self) -> None:
        a = _canonicalise_url("https://example.com/page/")
        b = _canonicalise_url("https://example.com/page")
        c = _canonicalise_url("https://example.com/page#section")
        assert a == b == c


# ---------------------------------------------------------------------------
# _build_candidate_set
# ---------------------------------------------------------------------------


class TestBuildCandidateSet:
    def test_start_url_is_first_candidate(self) -> None:
        candidates = _build_candidate_set(
            start_url="https://example.com",
            sitemap_urls=["https://example.com/page-a"],
            bfs_seed_urls=["https://example.com/page-b"],
            base_domain="example.com",
            max_pages=10,
            include_patterns=None,
        )
        assert candidates[0] == "https://example.com"

    def test_sitemap_takes_priority_on_cap(self) -> None:
        # max_pages=3: start_url + 2 sitemap entries should win over
        # any BFS-discovered URL even if BFS came first lexically.
        candidates = _build_candidate_set(
            start_url="https://example.com",
            sitemap_urls=[
                "https://example.com/sitemap-1",
                "https://example.com/sitemap-2",
                "https://example.com/sitemap-3",
            ],
            bfs_seed_urls=[
                "https://example.com/bfs-1",
                "https://example.com/bfs-2",
            ],
            base_domain="example.com",
            max_pages=3,
            include_patterns=None,
        )
        assert candidates == [
            "https://example.com",
            "https://example.com/sitemap-1",
            "https://example.com/sitemap-2",
        ]

    def test_dedup_via_canonicalisation(self) -> None:
        candidates = _build_candidate_set(
            start_url="https://example.com",
            sitemap_urls=[
                "https://example.com/page",
                "https://example.com/page/",  # same canonical key
                "https://example.com/page#frag",  # same canonical key
            ],
            bfs_seed_urls=[],
            base_domain="example.com",
            max_pages=10,
            include_patterns=None,
        )
        # Only one canonical /page entry, plus start_url.
        assert candidates == [
            "https://example.com",
            "https://example.com/page",
        ]

    def test_cross_domain_filtered_out(self) -> None:
        candidates = _build_candidate_set(
            start_url="https://example.com",
            sitemap_urls=["https://other.com/external"],
            bfs_seed_urls=["https://example.com/local"],
            base_domain="example.com",
            max_pages=10,
            include_patterns=None,
        )
        assert "https://other.com/external" not in candidates
        assert "https://example.com/local" in candidates

    def test_include_patterns_filters_substrings(self) -> None:
        candidates = _build_candidate_set(
            start_url="https://wiki.example/nl",  # start_url survives even if it doesn't match
            sitemap_urls=[
                "https://wiki.example/nl/getting-started",
                "https://wiki.example/en/getting-started",
            ],
            bfs_seed_urls=[
                "https://wiki.example/nl/blog",
                "https://wiki.example/en/blog",
            ],
            base_domain="wiki.example",
            max_pages=10,
            include_patterns=["/nl/"],
        )
        # start_url itself doesn't match "/nl/" (it's "/nl"), so it's filtered.
        # All other candidates MUST match "/nl/".
        assert "https://wiki.example/en/getting-started" not in candidates
        assert "https://wiki.example/en/blog" not in candidates
        assert "https://wiki.example/nl/getting-started" in candidates
        assert "https://wiki.example/nl/blog" in candidates


# ---------------------------------------------------------------------------
# _classify_fetch_outcome
# ---------------------------------------------------------------------------


class TestClassifyFetchOutcome:
    def test_success_when_result_success_true(self) -> None:
        assert (
            _classify_fetch_outcome({"success": True, "status_code": 200})
            == FetchReasonCode.SUCCESS.value
        )

    def test_429_classifies_rate_limited(self) -> None:
        assert (
            _classify_fetch_outcome({"success": False, "status_code": 429})
            == FetchReasonCode.RATE_LIMITED.value
        )

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_codes_classify_auth_error(self, status: int) -> None:
        assert (
            _classify_fetch_outcome({"success": False, "status_code": status})
            == FetchReasonCode.AUTH_ERROR.value
        )

    def test_other_4xx_classifies_http_4xx(self) -> None:
        assert (
            _classify_fetch_outcome({"success": False, "status_code": 404})
            == FetchReasonCode.HTTP_4XX.value
        )

    def test_5xx_classifies_http_5xx(self) -> None:
        assert (
            _classify_fetch_outcome({"success": False, "status_code": 503})
            == FetchReasonCode.HTTP_5XX.value
        )

    def test_timeout_message_classifies_timeout(self) -> None:
        assert (
            _classify_fetch_outcome(
                {"success": False, "status_code": None, "error_message": "Operation timeout"}
            )
            == FetchReasonCode.TIMEOUT.value
        )

    def test_dns_message_classifies_dns_error(self) -> None:
        assert (
            _classify_fetch_outcome(
                {"success": False, "status_code": None, "error_message": "DNS lookup failed"}
            )
            == FetchReasonCode.DNS_ERROR.value
        )

    def test_unknown_falls_through_to_unknown_exception(self) -> None:
        assert (
            _classify_fetch_outcome({"success": False, "status_code": None, "error_message": ""})
            == FetchReasonCode.UNKNOWN_EXCEPTION.value
        )

    def test_transport_timeout_exception_classifies_timeout(self) -> None:
        assert (
            _classify_fetch_outcome(None, error=httpx.ReadTimeout("timed out"))
            == FetchReasonCode.TIMEOUT.value
        )

    def test_transport_connect_error_classifies_connection_error(self) -> None:
        assert (
            _classify_fetch_outcome(None, error=httpx.ConnectError("refused"))
            == FetchReasonCode.CONNECTION_ERROR.value
        )

    def test_transport_dns_message_classifies_dns_error(self) -> None:
        # Generic exception with DNS-flavoured message — common in glibc errors.
        assert (
            _classify_fetch_outcome(None, error=RuntimeError("nodename nor servname provided"))
            == FetchReasonCode.DNS_ERROR.value
        )


# ---------------------------------------------------------------------------
# crawl_site — outcomes shape on transport failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawl_site_bulk_transport_failure_records_one_outcome_per_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4: even when the whole bulk request fails, every candidate URL
    gets an outcome record. No URL is silently lost."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return [
            "https://example.com/page-a",
            "https://example.com/page-b",
            "https://example.com/page-c",
        ]

    async def _fake_seed(url: str, **_kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=url,
            fit_markdown="seed",
            raw_markdown="seed",
            html="<html></html>",
            word_count=1,
            success=True,
            links={"internal": []},
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    monkeypatch.setattr(crawl4ai_client, "crawl_page", _fake_seed)

    async def _fake_post(self: httpx.AsyncClient, url: str, **_kwargs: Any) -> httpx.Response:
        raise httpx.ReadTimeout("simulated bulk timeout")

    with patch("httpx.AsyncClient.post", new=_fake_post):
        results, outcomes = await crawl4ai_client.crawl_site(
            start_url="https://example.com",
            max_pages=10,
        )

    # Whole-batch transport failure: no successful CrawlResults but every
    # candidate (start_url + 3 sitemap entries) gets a TIMEOUT outcome.
    assert results == []
    assert len(outcomes) == 4
    for outcome in outcomes:
        assert outcome["reason_code"] == FetchReasonCode.TIMEOUT.value
        assert outcome["status_code"] is None
        # Shape sanity — all four required keys present.
        assert set(outcome.keys()) == {"url", "reason_code", "status_code", "content_length"}


@pytest.mark.asyncio
async def test_crawl_site_returns_one_outcome_per_candidate_on_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4 + AC-5: per-URL outcomes classify each candidate distinctly."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return [
            "https://example.com/ok",
            "https://example.com/missing",
            "https://example.com/server-error",
        ]

    async def _fake_seed(url: str, **_kwargs: Any) -> CrawlResult:
        return CrawlResult(
            url=url,
            fit_markdown="x",
            raw_markdown="x",
            html="<html></html>",
            word_count=1,
            success=True,
            links={"internal": []},
        )

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    monkeypatch.setattr(crawl4ai_client, "crawl_page", _fake_seed)

    response_body = {
        "results": [
            {
                "url": "https://example.com",
                "success": True,
                "status_code": 200,
                "html": "<html>seed</html>",
                "markdown": "seed",
                "links": {"internal": []},
                "media": {},
            },
            {
                "url": "https://example.com/ok",
                "success": True,
                "status_code": 200,
                "html": "<html>ok</html>",
                "markdown": "ok",
                "links": {"internal": []},
                "media": {},
            },
            {
                "url": "https://example.com/missing",
                "success": False,
                "status_code": 404,
                "error_message": "Not Found",
                "html": "",
                "markdown": "",
                "links": {"internal": []},
                "media": {},
            },
            {
                "url": "https://example.com/server-error",
                "success": False,
                "status_code": 503,
                "error_message": "Service Unavailable",
                "html": "",
                "markdown": "",
                "links": {"internal": []},
                "media": {},
            },
        ]
    }

    async def _fake_post(self: httpx.AsyncClient, url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=response_body, request=request)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        results, outcomes = await crawl4ai_client.crawl_site(
            start_url="https://example.com",
            max_pages=10,
        )

    assert len(outcomes) == 4
    by_url = {o["url"]: o for o in outcomes}
    assert by_url["https://example.com"]["reason_code"] == FetchReasonCode.SUCCESS.value
    assert by_url["https://example.com/ok"]["reason_code"] == FetchReasonCode.SUCCESS.value
    assert by_url["https://example.com/missing"]["reason_code"] == FetchReasonCode.HTTP_4XX.value
    server_error_outcome = by_url["https://example.com/server-error"]
    assert server_error_outcome["reason_code"] == FetchReasonCode.HTTP_5XX.value
    # Two same-domain successful pages reach the ingest loop.
    assert len(results) == 2
    assert {r.url for r in results} == {"https://example.com", "https://example.com/ok"}
