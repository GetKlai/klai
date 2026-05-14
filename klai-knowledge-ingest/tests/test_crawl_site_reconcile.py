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
    _combine_bulk_responses,
    _fetch_sitemap_document,
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

    def test_www_and_apex_are_same_site_but_return_base_host(self) -> None:
        candidates = _build_candidate_set(
            start_url="https://www.example.com/blog",
            sitemap_urls=["https://example.com/blog/post-a"],
            bfs_seed_urls=[],
            base_domain="www.example.com",
            max_pages=10,
            include_patterns=["/blog/*"],
        )
        assert "https://www.example.com/blog/post-a" in candidates
        assert "https://example.com/blog/post-a" not in candidates


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


# ---------------------------------------------------------------------------
# sitemap discovery
# ---------------------------------------------------------------------------


class _FakeSitemapClient:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses

    async def get(self, url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        body = self.responses.get(url)
        if body is None:
            return httpx.Response(404, text="missing", request=request)
        return httpx.Response(200, text=body, request=request)


@pytest.mark.asyncio
async def test_fetch_sitemap_document_follows_index_and_coerces_apex_to_www() -> None:
    client = _FakeSitemapClient(
        {
            "https://www.example.com/sitemap-index.xml": """
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <sitemap><loc>https://example.com/sitemap-0.xml</loc></sitemap>
            </sitemapindex>
            """,
            "https://example.com/sitemap-0.xml": """
            <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <url><loc>https://example.com/blog/post-a/</loc></url>
              <url><loc>https://other.example/blog/post-b/</loc></url>
            </urlset>
            """,
        }
    )

    urls = await _fetch_sitemap_document(
        client,  # type: ignore[arg-type]
        sitemap_url="https://www.example.com/sitemap-index.xml",
        base_domain="www.example.com",
        seen_sitemaps=set(),
        depth=0,
    )

    assert urls == ["https://www.example.com/blog/post-a/"]

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


def _seed(url: str, *, success: bool = True, internal: list[str] | None = None) -> CrawlResult:
    """Build a CrawlResult shaped like a seed-page response."""
    return CrawlResult(
        url=url,
        fit_markdown="seed" if success else "",
        raw_markdown="seed" if success else "",
        html="<html></html>" if success else "",
        word_count=1 if success else 0,
        success=success,
        links={"internal": [{"href": h, "text": ""} for h in (internal or [])]},
    )


def _patch_seed(monkeypatch: pytest.MonkeyPatch, seed_result: CrawlResult) -> None:
    async def _fake(*, start_url: str, **_kwargs: Any) -> CrawlResult:
        return seed_result

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake)


@pytest.mark.asyncio
async def test_crawl_site_bulk_transport_failure_records_one_outcome_per_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4: even when the whole bulk request fails, every candidate URL
    (seed + bulk) gets an outcome record. No URL is silently lost."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return [
            "https://example.com/page-a",
            "https://example.com/page-b",
            "https://example.com/page-c",
        ]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

    async def _fake_bfs(**_kwargs: Any) -> tuple[list[CrawlResult], None]:
        return [_seed("https://example.com")], None

    monkeypatch.setattr(crawl4ai_client, "_bfs_deep_crawl", _fake_bfs)

    async def _fake_post(self: httpx.AsyncClient, url: str, **_kwargs: Any) -> httpx.Response:
        raise httpx.ReadTimeout("simulated bulk timeout")

    with patch("httpx.AsyncClient.post", new=_fake_post):
        results, outcomes = await crawl4ai_client.crawl_site(
            start_url="https://example.com",
            max_pages=10,
        )

    # Seed (start_url) succeeded → 1 result, 1 SUCCESS outcome.
    # Bulk timed out → 3 candidates each get a TIMEOUT outcome.
    assert len(results) == 1
    assert results[0].url == "https://example.com"
    assert len(outcomes) == 4

    by_url = {o["url"]: o for o in outcomes}
    assert by_url["https://example.com"]["reason_code"] == FetchReasonCode.SUCCESS.value
    for url in ("https://example.com/page-a", "https://example.com/page-b", "https://example.com/page-c"):
        assert by_url[url]["reason_code"] == FetchReasonCode.TIMEOUT.value
        assert by_url[url]["status_code"] is None

    # Shape sanity — all four required keys present on every outcome.
    for outcome in outcomes:
        assert set(outcome.keys()) == {"url", "reason_code", "status_code", "content_length"}


@pytest.mark.asyncio
async def test_crawl_site_returns_one_outcome_per_candidate_on_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-4 + AC-5: per-URL outcomes classify each candidate distinctly,
    including the seed."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return [
            "https://example.com/ok",
            "https://example.com/missing",
            "https://example.com/server-error",
        ]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)

    async def _fake_bfs(**_kwargs: Any) -> tuple[list[CrawlResult], None]:
        return [_seed("https://example.com")], None

    monkeypatch.setattr(crawl4ai_client, "_bfs_deep_crawl", _fake_bfs)

    # Bulk response — start_url is NOT in the request body, so the response
    # body MUST NOT include it either.
    bulk_response = {
        "results": [
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
        return httpx.Response(200, json=bulk_response, request=request)

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
    assert (
        by_url["https://example.com/server-error"]["reason_code"]
        == FetchReasonCode.HTTP_5XX.value
    )
    # Two same-domain successful pages reach the ingest loop: seed + /ok.
    assert {r.url for r in results} == {"https://example.com", "https://example.com/ok"}


# ---------------------------------------------------------------------------
# Followup PR — start_url is fetched once (no bulk re-fetch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawl_site_does_not_double_fetch_start_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seed call covers ``start_url``; the bulk submission MUST NOT include it."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return ["https://example.com/page-a"]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    _patch_seed(monkeypatch, _seed("https://example.com"))

    captured: dict[str, Any] = {}

    async def _fake_post(self: httpx.AsyncClient, url: str, **kwargs: Any) -> httpx.Response:
        captured["payload"] = kwargs.get("json")
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"results": []}, request=request)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        await crawl4ai_client.crawl_site(start_url="https://example.com", max_pages=10)

    urls_submitted = captured["payload"]["urls"]
    assert "https://example.com" not in urls_submitted, (
        "start_url must not appear in the bulk submission — that would re-fetch the seed"
    )
    assert urls_submitted == ["https://example.com/page-a"]


# ---------------------------------------------------------------------------
# Followup PR — login_indicator_selector is propagated to the seed config.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_config_carries_login_indicator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed fetch MUST use the same crawler_config (incl. login_indicator) as bulk.

    Regression guard: an earlier implementation used ``crawl_page(start_url)``
    for the seed, and ``crawl_page`` builds its own config without the
    login_indicator kwarg. That meant for auth-walled sites the seed
    "succeeded" on the login page, the BFS link list became
    login-form anchors, and the bulk ran into AUTH_ERROR on every URL.
    """
    captured_bfs_config: dict[str, Any] = {}

    async def _fake_bfs(
        *,
        crawler_config: dict[str, Any],
        **_kwargs: Any,
    ) -> tuple[list[CrawlResult], None]:
        captured_bfs_config.update(crawler_config)
        return [_seed("https://wiki.example")], None

    monkeypatch.setattr(crawl4ai_client, "_bfs_deep_crawl", _fake_bfs)
    monkeypatch.setattr(
        crawl4ai_client, "_fetch_sitemap_urls", lambda _base: _async_return([])
    )

    async def _fake_post(self: httpx.AsyncClient, url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"results": []}, request=request)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        await crawl4ai_client.crawl_site(
            start_url="https://wiki.example",
            login_indicator_selector="#loginForm",
        )

    # The BFS config MUST reflect the login_indicator: build_crawl_config
    # negates the wait_for expression with the indicator selector when set.
    wait_for = captured_bfs_config.get("wait_for", "")
    assert "#loginForm" in wait_for, (
        f"seed config did not propagate login_indicator_selector — wait_for was {wait_for!r}"
    )


def _async_return(value: Any):
    """Wrap a value in an awaitable so it can be used as an async stub return."""

    async def _coro(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _coro()


# ---------------------------------------------------------------------------
# Followup PR — redirect-aware response matching (positional fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawl_site_matches_redirect_response_positionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate that gets redirected (response.url != candidate.url)
    MUST still match — not become UNKNOWN_EXCEPTION via canonical miss."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return [
            "https://example.com/old-page",  # this one will redirect
            "https://example.com/stable",  # direct hit
        ]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    _patch_seed(monkeypatch, _seed("https://example.com"))

    bulk_response = {
        "results": [
            # response[0].url is the REDIRECT TARGET, not the candidate
            {
                "url": "https://example.com/new-page",
                "success": True,
                "status_code": 200,
                "html": "<html>redirected content</html>",
                "markdown": "redirected content",
                "links": {"internal": []},
                "media": {},
            },
            # response[1] matches the candidate directly
            {
                "url": "https://example.com/stable",
                "success": True,
                "status_code": 200,
                "html": "<html>stable</html>",
                "markdown": "stable",
                "links": {"internal": []},
                "media": {},
            },
        ]
    }

    async def _fake_post(self: httpx.AsyncClient, url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json=bulk_response, request=request)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        results, outcomes = await crawl4ai_client.crawl_site(
            start_url="https://example.com",
            max_pages=10,
        )

    by_url = {o["url"]: o for o in outcomes}
    # The redirected candidate MUST be classified as SUCCESS, not UNKNOWN_EXCEPTION.
    assert by_url["https://example.com/old-page"]["reason_code"] == FetchReasonCode.SUCCESS.value
    assert by_url["https://example.com/stable"]["reason_code"] == FetchReasonCode.SUCCESS.value
    # Both bulk candidates produced ingestable results (plus the seed).
    result_urls = {r.url for r in results}
    assert "https://example.com/new-page" in result_urls  # post-redirect URL preserved
    assert "https://example.com/stable" in result_urls


# ---------------------------------------------------------------------------
# Followup PR — _build_candidate_set with include_start_url=False
# ---------------------------------------------------------------------------


class TestBuildCandidateSetExcludeStartUrl:
    def test_excludes_start_url_from_output(self) -> None:
        candidates = _build_candidate_set(
            start_url="https://example.com",
            sitemap_urls=["https://example.com/page-a"],
            bfs_seed_urls=["https://example.com/page-b"],
            base_domain="example.com",
            max_pages=10,
            include_patterns=None,
            include_start_url=False,
        )
        assert "https://example.com" not in candidates
        assert candidates == [
            "https://example.com/page-a",
            "https://example.com/page-b",
        ]

    def test_excludes_start_url_alias_in_dedupe(self) -> None:
        """A sitemap entry that canonical-equals start_url is also excluded
        when include_start_url=False — guards against re-introducing the
        double fetch via a sitemap that lists the homepage."""
        candidates = _build_candidate_set(
            start_url="https://example.com",
            # Variant spellings of start_url that all canonicalise to it.
            sitemap_urls=[
                "https://example.com/",  # trailing slash on root → canonical "/"
                "HTTPS://Example.com",  # mixed case
                "https://example.com#frag",  # fragment
                "https://example.com/page",  # legitimate non-homepage
            ],
            bfs_seed_urls=[],
            base_domain="example.com",
            max_pages=10,
            include_patterns=None,
            include_start_url=False,
        )
        assert "https://example.com/page" in candidates
        # None of the start_url-variant spellings should leak through.
        for candidate in candidates:
            assert candidate not in (
                "https://example.com",
                "https://example.com/",
                "HTTPS://Example.com",
                "https://example.com#frag",
            ), f"start_url variant {candidate} leaked through include_start_url=False"


# ---------------------------------------------------------------------------
# _combine_bulk_responses — same-domain guard on positional fallback
# ---------------------------------------------------------------------------


class TestCombineBulkResponsesPositionalGuard:
    """The positional fallback only matches a same-origin response.

    Without the guard, an out-of-order or mis-routed response from
    crawl4ai's MemoryAdaptiveDispatcher could silently shadow a candidate
    with content from a completely different site, surfacing as a fake
    SUCCESS/HTTP_5XX outcome on the wrong URL. The same-domain check
    forces such corruption to fall through to UNKNOWN_EXCEPTION (visible
    to operators) rather than mislabel.
    """

    def test_positional_match_accepted_for_same_domain_redirect(self) -> None:
        """A canonical-miss + same-domain positional response = redirect.
        That response is accepted, and the outcome's reason_code reflects
        the redirected page's success state."""
        candidates = ["https://example.com/old-page"]
        raw_results = [
            {
                "url": "https://example.com/new-page",  # redirected
                "success": True,
                "status_code": 200,
                "html": "<html>content</html>",
                "markdown": "content",
                "links": {"internal": []},
                "media": {},
            }
        ]
        results, outcomes = _combine_bulk_responses(
            candidates=candidates,
            raw_results=raw_results,
            transport_error=None,
            base_domain="example.com",
        )
        assert len(outcomes) == 1
        assert outcomes[0]["url"] == "https://example.com/old-page"
        assert outcomes[0]["reason_code"] == FetchReasonCode.SUCCESS.value
        assert outcomes[0]["status_code"] == 200
        # The CrawlResult inherits the response's URL (post-redirect),
        # which is correct — the ingest pipeline ingests the resolved page.
        assert len(results) == 1

    def test_positional_match_rejected_for_cross_domain_response(self) -> None:
        """If the positional response is on a different domain (e.g. crawl4ai
        reordered the response list under load), the fallback MUST refuse
        to claim it. The candidate falls through to UNKNOWN_EXCEPTION."""
        candidates = ["https://example.com/old-page"]
        raw_results = [
            {
                # WRONG site — out-of-order response from a parallel batch.
                "url": "https://attacker.com/exploit",
                "success": True,
                "status_code": 200,
                "html": "<html>not ours</html>",
                "markdown": "not ours",
                "links": {"internal": []},
                "media": {},
            }
        ]
        results, outcomes = _combine_bulk_responses(
            candidates=candidates,
            raw_results=raw_results,
            transport_error=None,
            base_domain="example.com",
        )
        # Outcome surfaces as UNKNOWN_EXCEPTION, NOT as a fake SUCCESS on
        # the candidate URL with attacker.com's content.
        assert outcomes[0]["url"] == "https://example.com/old-page"
        assert outcomes[0]["reason_code"] == FetchReasonCode.UNKNOWN_EXCEPTION.value
        # No CrawlResult — we refused to fabricate one from cross-domain data.
        assert results == []

    def test_canonical_match_unaffected_by_same_domain_guard(self) -> None:
        """Direct canonical match still works regardless of domain logic."""
        candidates = ["https://example.com/page"]
        raw_results = [
            {
                "url": "https://example.com/page",
                "success": True,
                "status_code": 200,
                "html": "<html>content</html>",
                "markdown": "content",
                "links": {"internal": []},
                "media": {},
            }
        ]
        results, outcomes = _combine_bulk_responses(
            candidates=candidates,
            raw_results=raw_results,
            transport_error=None,
            base_domain="example.com",
        )
        assert outcomes[0]["reason_code"] == FetchReasonCode.SUCCESS.value
        assert len(results) == 1

    def test_noindex_follow_page_is_outcome_but_not_ingested(self) -> None:
        """Robots noindex means crawl for discovery, skip as a KB document."""
        candidates = ["https://example.com/blog/tag/AI"]
        raw_results = [
            {
                "url": "https://example.com/blog/tag/AI",
                "success": True,
                "status_code": 200,
                "html": (
                    "<html><head>"
                    '<meta name="robots" content="noindex,follow">'
                    '<meta property="og:type" content="website">'
                    "</head><body>Tag archive</body></html>"
                ),
                "markdown": "Tag archive",
                "links": {"internal": [{"href": "https://example.com/blog/post", "text": ""}]},
                "media": {},
            }
        ]
        results, outcomes = _combine_bulk_responses(
            candidates=candidates,
            raw_results=raw_results,
            transport_error=None,
            base_domain="example.com",
        )

        assert outcomes[0]["reason_code"] == FetchReasonCode.NON_CONTENT_LISTING_PAGE.value
        assert results == []

    def test_article_metadata_overrides_link_heavy_shape(self) -> None:
        """Article metadata keeps a real post ingestable even with many links."""
        candidates = ["https://example.com/blog/post"]
        raw_results = [
            {
                "url": "https://example.com/blog/post",
                "success": True,
                "status_code": 200,
                "html": (
                    "<html><head>"
                    '<meta property="og:type" content="article">'
                    '<script type="application/ld+json">'
                    '{"@context":"https://schema.org","@type":"BlogPosting"}'
                    "</script>"
                    "</head><body>Real article content</body></html>"
                ),
                "markdown": "Real article content",
                "links": {
                    "internal": [
                        {"href": f"https://example.com/blog/related-{i}", "text": ""}
                        for i in range(25)
                    ]
                },
                "media": {},
            }
        ]
        results, outcomes = _combine_bulk_responses(
            candidates=candidates,
            raw_results=raw_results,
            transport_error=None,
            base_domain="example.com",
        )

        assert outcomes[0]["reason_code"] == FetchReasonCode.SUCCESS.value
        assert [r.url for r in results] == ["https://example.com/blog/post"]
