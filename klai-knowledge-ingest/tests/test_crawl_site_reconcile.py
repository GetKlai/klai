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
from knowledge_ingest.config import settings
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
        assert _canonicalise_url("https://example.com/page#section") == "https://example.com/page"

    def test_lowercases_scheme_and_host(self) -> None:
        assert _canonicalise_url("HTTPS://Example.COM/Page") == "https://example.com/Page"

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


def _antibot_http_status_error(*, extra_marker: str = "") -> httpx.HTTPStatusError:
    """Build a 500 HTTPStatusError whose body reports an anti-bot block.

    Mirrors what crawl4ai's REST API actually returns for a Cloudflare JS
    challenge — same shape as the "minimal content" flavour covered in
    ``test_fetch_seed_retries_relaxed_config_after_minimal_content_antibot``
    (test_crawl4ai_filter_chain.py), generalised: no thin-content marker
    required, just the "blocked by anti-bot protection" phrase in the body.

    NOTE (2026-08-14): this body shape does NOT match what crawl4ai
    actually returns for a real bulk-request 500 in production — see
    ``_opaque_bulk_500_http_status_error`` below for that. This helper is
    kept only for the confirmed dict-shaped path (crawl4ai returning HTTP
    200 with an explicit "blocked by anti-bot protection" error_message)
    and the pre-existing seed thin-content heuristic.
    """
    request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
    response = httpx.Response(
        500,
        json={"detail": f"Blocked by anti-bot protection: Cloudflare JS challenge{extra_marker}"},
        request=request,
    )
    return httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)


def _opaque_bulk_500_http_status_error(
    *, correlation_id: str = "188834187d7d"
) -> httpx.HTTPStatusError:
    """Build a 500 HTTPStatusError with crawl4ai's REAL production bulk body.

    2026-08-14 intermedia.com incident: a live bulk request to crawl4ai for
    blocked pages returned exactly this shape — no diagnosable reason, no
    "blocked by anti-bot protection" marker, just an opaque error +
    correlation_id. This is the body every bulk-5xx transport error in
    production actually carries.
    """
    request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
    response = httpx.Response(
        500,
        json={"error": "Internal server error", "correlation_id": correlation_id},
        request=request,
    )
    return httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)


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

    def test_opaque_bulk_500_transport_error_classifies_http_5xx(self) -> None:
        """2026-08-14 intermedia.com: crawl4ai's REAL production bulk-500
        body is opaque (no diagnosable reason) — it must classify honestly
        as HTTP_5XX, never BLOCKED_ANTI_BOT (unproven) and never
        unknown_exception (the pre-fix bug: PR #945's body-match never
        fired against this exact shape, so every one of the 17 failed
        intermedia.com pages classified unknown_exception instead)."""
        exc = _opaque_bulk_500_http_status_error()
        assert _classify_fetch_outcome(None, error=exc) == FetchReasonCode.HTTP_5XX.value

    def test_antibot_marker_500_transport_error_also_classifies_http_5xx(self) -> None:
        """Regression guard: even a 500 whose body DOES contain the old
        "blocked by anti-bot protection" marker must classify as HTTP_5XX
        now — the transport-error-path body match was retired 2026-08-14
        because it never fires against real crawl4ai responses; status
        code is now the only signal used on the raised-exception path."""
        exc = _antibot_http_status_error()
        assert _classify_fetch_outcome(None, error=exc) == FetchReasonCode.HTTP_5XX.value

    def test_4xx_transport_error_classifies_http_4xx(self) -> None:
        request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
        response = httpx.Response(404, json={"detail": "Not Found"}, request=request)
        exc = httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)
        assert _classify_fetch_outcome(None, error=exc) == FetchReasonCode.HTTP_4XX.value

    def test_429_transport_error_classifies_rate_limited(self) -> None:
        request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
        response = httpx.Response(429, json={"detail": "Too Many Requests"}, request=request)
        exc = httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)
        assert _classify_fetch_outcome(None, error=exc) == FetchReasonCode.RATE_LIMITED.value

    def test_antibot_blocked_error_message_in_page_result_classifies_blocked_anti_bot(
        self,
    ) -> None:
        """Dict/page-result path: the ONE confirmed real path to
        BLOCKED_ANTI_BOT — crawl4ai returning HTTP 200 with
        results[i].success=false and an explicit "blocked by anti-bot
        protection" marker inline in error_message. Kept intentionally
        (see _classify_fetch_outcome's dict-path comment); unlike the
        transport-exception body match retired 2026-08-14, this shape is
        real."""
        assert (
            _classify_fetch_outcome(
                {
                    "success": False,
                    "status_code": None,
                    "error_message": (
                        '{"detail":"Blocked by anti-bot protection: Cloudflare JS challenge"}'
                    ),
                }
            )
            == FetchReasonCode.BLOCKED_ANTI_BOT.value
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
    _patch_seed(monkeypatch, _seed("https://example.com"))

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
    for url in (
        "https://example.com/page-a",
        "https://example.com/page-b",
        "https://example.com/page-c",
    ):
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
    _patch_seed(monkeypatch, _seed("https://example.com"))

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
        by_url["https://example.com/server-error"]["reason_code"] == FetchReasonCode.HTTP_5XX.value
    )
    # Two same-domain successful pages reach the ingest loop: seed + /ok.
    assert {r.url for r in results} == {"https://example.com", "https://example.com/ok"}


@pytest.mark.asyncio
async def test_crawl_site_frontier_fetches_listing_children(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Links discovered on fetched listing pages must enter the Klai frontier."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return []

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    _patch_seed(
        monkeypatch,
        _seed(
            "https://wiki.redcactus.cloud/nl",
            internal=["https://wiki.redcactus.cloud/nl/crm-software"],
        ),
    )

    async def _fake_bulk_fetch(
        *,
        urls: list[str],
        **_kwargs: Any,
    ) -> tuple[list[dict[str, Any]], BaseException | None]:
        pages: list[dict[str, Any]] = []
        for url in urls:
            if url == "https://wiki.redcactus.cloud/nl/crm-software":
                pages.append(
                    {
                        "url": url,
                        "success": True,
                        "status_code": 200,
                        "html": "<html>CRM listing</html>",
                        "markdown": "CRM listing",
                        "links": {
                            "internal": [
                                {
                                    "href": "https://wiki.redcactus.cloud/nl/crm-software/zoho-bigin",
                                    "text": "Zoho Bigin",
                                },
                                {
                                    "href": "https://wiki.redcactus.cloud/nl/crm-software/zoho-crm",
                                    "text": "Zoho CRM",
                                },
                                {
                                    "href": "https://wiki.redcactus.cloud/nl/crm-software/zoho-desk",
                                    "text": "Zoho Desk",
                                },
                            ]
                        },
                        "media": {},
                    }
                )
            else:
                pages.append(
                    {
                        "url": url,
                        "success": True,
                        "status_code": 200,
                        "html": f"<html>{url}</html>",
                        "markdown": url.rsplit("/", 1)[-1],
                        "links": {"internal": []},
                        "media": {},
                    }
                )
        return pages, None

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_bulk_fetch)

    results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://wiki.redcactus.cloud/nl",
        max_depth=2,
        max_pages=10,
        include_patterns=["/nl/"],
    )

    outcome_urls = {outcome["url"] for outcome in outcomes}
    assert "https://wiki.redcactus.cloud/nl/crm-software/zoho-bigin" in outcome_urls
    assert "https://wiki.redcactus.cloud/nl/crm-software/zoho-crm" in outcome_urls
    assert "https://wiki.redcactus.cloud/nl/crm-software/zoho-desk" in outcome_urls
    assert {
        "https://wiki.redcactus.cloud/nl/crm-software/zoho-bigin",
        "https://wiki.redcactus.cloud/nl/crm-software/zoho-crm",
        "https://wiki.redcactus.cloud/nl/crm-software/zoho-desk",
    }.issubset({result.url for result in results})


@pytest.mark.asyncio
async def test_crawl_site_records_budget_exhausted_for_unfetched_frontier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When max_pages stops the frontier, discovered URLs get explicit outcomes."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return []

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    _patch_seed(
        monkeypatch,
        _seed("https://example.com/nl", internal=["https://example.com/nl/crm-software"]),
    )

    async def _fake_bulk_fetch(
        *,
        urls: list[str],
        **_kwargs: Any,
    ) -> tuple[list[dict[str, Any]], BaseException | None]:
        assert urls == ["https://example.com/nl/crm-software"]
        return [
            {
                "url": "https://example.com/nl/crm-software",
                "success": True,
                "status_code": 200,
                "html": "<html>CRM listing</html>",
                "markdown": "CRM listing",
                "links": {
                    "internal": [
                        {"href": "https://example.com/nl/crm-software/zoho-bigin", "text": ""},
                        {"href": "https://example.com/nl/crm-software/zoho-crm", "text": ""},
                        {"href": "https://example.com/nl/crm-software/zoho-desk", "text": ""},
                    ]
                },
                "media": {},
            }
        ], None

    monkeypatch.setattr(crawl4ai_client, "_chunked_bulk_fetch", _fake_bulk_fetch)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com/nl",
        max_depth=2,
        max_pages=2,
        include_patterns=["/nl/"],
    )

    by_url = {outcome["url"]: outcome for outcome in outcomes}
    assert (
        by_url["https://example.com/nl/crm-software/zoho-bigin"]["reason_code"]
        == FetchReasonCode.NOT_FETCHED_BUDGET_EXHAUSTED.value
    )
    assert (
        by_url["https://example.com/nl/crm-software/zoho-crm"]["reason_code"]
        == FetchReasonCode.NOT_FETCHED_BUDGET_EXHAUSTED.value
    )
    assert (
        by_url["https://example.com/nl/crm-software/zoho-desk"]["reason_code"]
        == FetchReasonCode.NOT_FETCHED_BUDGET_EXHAUSTED.value
    )


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
    captured_seed_config: dict[str, Any] = {}

    async def _fake_seed(
        *,
        crawler_config: dict[str, Any],
        **_kwargs: Any,
    ) -> CrawlResult:
        captured_seed_config.update(crawler_config)
        return _seed("https://wiki.example")

    monkeypatch.setattr(crawl4ai_client, "_fetch_seed_page", _fake_seed)
    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", lambda _base: _async_return([]))

    async def _fake_post(self: httpx.AsyncClient, url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"results": []}, request=request)

    with patch("httpx.AsyncClient.post", new=_fake_post):
        await crawl4ai_client.crawl_site(
            start_url="https://wiki.example",
            login_indicator_selector="#loginForm",
        )

    # The seed config MUST reflect the login_indicator: build_crawl_config
    # negates the wait_for expression with the indicator selector when set.
    wait_for = captured_seed_config.get("wait_for", "")
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


# ---------------------------------------------------------------------------
# _combine_bulk_responses — bulk 5xx transport_error (2026-08-14)
# ---------------------------------------------------------------------------


def test_combine_bulk_responses_opaque_5xx_transport_error_all_http_5xx() -> None:
    """When the whole bulk batch fails with crawl4ai's REAL opaque 500 body,
    every candidate gets HTTP_5XX — not unknown_exception, and not a
    guessed BLOCKED_ANTI_BOT — so the caller (crawl_site) can detect it and
    trigger sequential recovery (see _is_bulk_5xx_error)."""
    candidates = [
        "https://intermedia.com/products/unite",
        "https://intermedia.com/products/ai",
    ]
    results, outcomes = _combine_bulk_responses(
        candidates=candidates,
        raw_results=[],
        transport_error=_opaque_bulk_500_http_status_error(),
        base_domain="intermedia.com",
    )
    assert results == []
    assert len(outcomes) == 2
    for outcome, url in zip(outcomes, candidates, strict=True):
        assert outcome["url"] == url
        assert outcome["reason_code"] == FetchReasonCode.HTTP_5XX.value
        assert outcome["status_code"] is None


# ---------------------------------------------------------------------------
# crawl_site — sequential bulk-5xx recovery (2026-08-14, intermedia.com)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crawl_site_recovers_batch_via_sequential_bulk_5xx_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bulk fetch 500s the whole 3-url batch with crawl4ai's REAL opaque
    production body; sequential single-page retry recovers the URLs that
    pass the (intermittent) challenge and marks the rest HTTP_5XX
    (honestly — never a guessed BLOCKED_ANTI_BOT). Regression test for the
    exact intermedia.com production bug: /products/unite passed,
    /products/ai stayed blocked, seconds apart, via the same single-page
    path — and the bulk failure body carried no diagnosable reason at all."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return [
            "https://example.com/page-a",
            "https://example.com/page-b",
            "https://example.com/page-c",
        ]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    _patch_seed(monkeypatch, _seed("https://example.com"))

    def _success_page(url: str) -> dict[str, Any]:
        return {
            "url": url,
            "success": True,
            "status_code": 200,
            "html": "<html><body>Real page content, plenty of words here.</body></html>",
            "markdown": "Real page content, plenty of words here.",
            "links": {"internal": []},
            "media": {},
        }

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        urls = payload["urls"]
        if len(urls) > 1:
            # The bulk batch request — always fails wholesale with crawl4ai's
            # real, opaque production body (no diagnosable reason).
            request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
            response = httpx.Response(
                500,
                json={"error": "Internal server error", "correlation_id": "188834187d7d"},
                request=request,
            )
            raise httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)

        # Sequential single-page recovery request.
        (url,) = urls
        if url == "https://example.com/page-b":
            request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
            response = httpx.Response(
                500,
                json={"error": "Internal server error", "correlation_id": "188834187d7d"},
                request=request,
            )
            raise httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)
        return {"results": [_success_page(url)]}

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
    )

    by_url = {o["url"]: o for o in outcomes}
    assert by_url["https://example.com/page-a"]["reason_code"] == FetchReasonCode.SUCCESS.value
    assert by_url["https://example.com/page-b"]["reason_code"] == FetchReasonCode.HTTP_5XX.value
    assert by_url["https://example.com/page-c"]["reason_code"] == FetchReasonCode.SUCCESS.value

    result_urls = {r.url for r in results}
    assert "https://example.com/page-a" in result_urls
    assert "https://example.com/page-c" in result_urls
    assert "https://example.com/page-b" not in result_urls


@pytest.mark.asyncio
async def test_crawl_site_bulk_5xx_recovery_respects_and_logs_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the sequential-recovery budget is exhausted mid-batch, the
    remaining URLs are marked HTTP_5XX WITHOUT a network call (honest, not
    a guessed anti-bot verdict), and the cap event is logged exactly
    once."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return [
            "https://example.com/page-a",
            "https://example.com/page-b",
            "https://example.com/page-c",
        ]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    _patch_seed(monkeypatch, _seed("https://example.com"))
    # Force the cap to trip after the very first sequential retry.
    monkeypatch.setattr(crawl4ai_client, "_MAX_SEQUENTIAL_RECOVERY", 1)

    attempted_urls: list[str] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        urls = payload["urls"]
        if len(urls) > 1:
            request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
            response = httpx.Response(
                500,
                json={"error": "Internal server error", "correlation_id": "188834187d7d"},
                request=request,
            )
            raise httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)

        (url,) = urls
        attempted_urls.append(url)
        return {
            "results": [
                {
                    "url": url,
                    "success": True,
                    "status_code": 200,
                    "html": "<html><body>Recovered page content, several words.</body></html>",
                    "markdown": "Recovered page content, several words.",
                    "links": {"internal": []},
                    "media": {},
                }
            ]
        }

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    with patch.object(crawl4ai_client.logger, "warning") as mock_warning:
        _results, outcomes = await crawl4ai_client.crawl_site(
            start_url="https://example.com",
            max_pages=10,
        )

    # Budget of 1: only the first URL in the batch is actually re-fetched.
    assert attempted_urls == ["https://example.com/page-a"]

    by_url = {o["url"]: o for o in outcomes}
    assert by_url["https://example.com/page-a"]["reason_code"] == FetchReasonCode.SUCCESS.value
    # Capped without a network call — both honestly HTTP_5XX, not a guessed
    # anti-bot verdict.
    assert by_url["https://example.com/page-b"]["reason_code"] == FetchReasonCode.HTTP_5XX.value
    assert by_url["https://example.com/page-c"]["reason_code"] == FetchReasonCode.HTTP_5XX.value

    cap_log_calls = [
        call
        for call in mock_warning.call_args_list
        if call.args[:1] == ("crawl_bulk_5xx_recovery_capped",)
    ]
    assert len(cap_log_calls) == 1
    _, kwargs = cap_log_calls[0]
    assert kwargs["recovered"] == 1
    assert kwargs["still_failing"] == 2
    assert kwargs["capped_at"] == 1
    assert kwargs["remaining"] == 2


@pytest.mark.asyncio
async def test_crawl_site_no_sequential_recovery_on_bulk_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bulk 4xx (e.g. a misconfigured request) must NOT trigger the
    sequential-recovery fallback — that path is reserved for 5xx, where
    the cause is genuinely unknowable. A 4xx across the whole batch is
    classified directly via _combine_bulk_responses, unchanged."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return ["https://example.com/page-a", "https://example.com/page-b"]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    _patch_seed(monkeypatch, _seed("https://example.com"))

    single_page_calls: list[str] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        urls = payload["urls"]
        if len(urls) == 1:
            single_page_calls.append(urls[0])
        request = httpx.Request("POST", "http://crawl4ai:11235/crawl")
        response = httpx.Response(400, json={"detail": "Bad Request"}, request=request)
        raise httpx.HTTPStatusError("crawl4ai failed", request=request, response=response)

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
    )

    # No sequential single-page recovery calls were made for the bulk batch.
    assert single_page_calls == []
    by_url = {o["url"]: o for o in outcomes}
    assert by_url["https://example.com/page-a"]["reason_code"] == FetchReasonCode.HTTP_4XX.value
    assert by_url["https://example.com/page-b"]["reason_code"] == FetchReasonCode.HTTP_4XX.value


@pytest.mark.asyncio
async def test_crawl_site_no_sequential_recovery_on_bulk_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bulk transport timeout (not an HTTPStatusError at all) must NOT
    trigger the sequential-recovery fallback — _is_bulk_5xx_error requires
    an httpx.HTTPStatusError with a 5xx status, which a timeout is not."""

    async def _fake_sitemap(_base: str) -> list[str]:
        return ["https://example.com/page-a", "https://example.com/page-b"]

    monkeypatch.setattr(crawl4ai_client, "_fetch_sitemap_urls", _fake_sitemap)
    _patch_seed(monkeypatch, _seed("https://example.com"))

    single_page_calls: list[str] = []

    async def _fake_crawl_sync(
        _client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        urls = payload["urls"]
        if len(urls) == 1:
            single_page_calls.append(urls[0])
        raise httpx.ReadTimeout("simulated bulk timeout")

    monkeypatch.setattr(crawl4ai_client, "_crawl_sync", _fake_crawl_sync)

    _results, outcomes = await crawl4ai_client.crawl_site(
        start_url="https://example.com",
        max_pages=10,
    )

    assert single_page_calls == []
    by_url = {o["url"]: o for o in outcomes}
    assert by_url["https://example.com/page-a"]["reason_code"] == FetchReasonCode.TIMEOUT.value
    assert by_url["https://example.com/page-b"]["reason_code"] == FetchReasonCode.TIMEOUT.value


# ---------------------------------------------------------------------------
# Sequential recovery pacing: cooldown, circuit breaker, wall-clock budget
#
# crawl4ai reuses ONE browser per crawl, so a flagged session stays flagged:
# measured on intermedia.com 2026-08-14, the 1st standalone fetch succeeded
# and the 2nd and 3rd both 500'd. Its pool drops an idle browser after ~53s,
# after which a fresh session passes again. The recovery therefore waits
# before every attempt, and bounds that waiting three ways.
# ---------------------------------------------------------------------------


def _blocked_result() -> CrawlResult:
    return CrawlResult(
        url="",
        fit_markdown="",
        raw_markdown="",
        html="",
        word_count=0,
        success=False,
        status_code=500,
        error_message="boom",
    )


def _ok_result(url: str) -> CrawlResult:
    body = "Real page content with plenty of genuine words in it, enough to ingest."
    return CrawlResult(
        url=url,
        fit_markdown=body,
        raw_markdown=body,
        html=f"<html><body><p>{body}</p></body></html>",
        word_count=len(body.split()),
        success=True,
        status_code=200,
    )


async def _recover(monkeypatch, urls, outcomes_by_url, *, budget=60):
    """Drive _recover_bulk_5xx_batch with scripted per-URL results."""
    slept: list[float] = []

    async def _record_sleep(seconds: float) -> None:
        slept.append(seconds)

    async def _fake_fetch(url, _config, *, cookies=None, selector=None, **_kw):
        return outcomes_by_url[url]

    monkeypatch.setattr(crawl4ai_client, "_recovery_sleep", _record_sleep)
    monkeypatch.setattr(crawl4ai_client, "_crawl_page_with_config", _fake_fetch)

    result = await crawl4ai_client._recover_bulk_5xx_batch(
        urls,
        crawler_config={},
        cookies=None,
        base_domain="example.com",
        recovery_budget=budget,
    )
    return result, slept


@pytest.mark.asyncio
async def test_recovery_cools_down_before_every_attempt_including_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bulk burst that just failed is what flagged the browser, so the
    first attempt needs the recycle window as much as the rest."""
    urls = [f"https://example.com/p{i}" for i in range(3)]
    scripted = {u: _ok_result(u) for u in urls}

    (_results, _links, outcomes, attempted), slept = await _recover(monkeypatch, urls, scripted)

    assert attempted == 3
    assert len(slept) == 3, "one cooldown per attempt, first one included"
    assert all(s == settings.crawl_sequential_recovery_cooldown_seconds for s in slept)
    assert all(o["reason_code"] == FetchReasonCode.SUCCESS.value for o in outcomes)


@pytest.mark.asyncio
async def test_recovery_circuit_opens_after_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run of failures despite the cooldown means the site is not
    recoverable — stop instead of burning a cooldown per remaining URL."""
    monkeypatch.setattr(settings, "crawl_sequential_recovery_max_consecutive_failures", 3)
    urls = [f"https://example.com/p{i}" for i in range(10)]
    scripted = {u: _blocked_result() for u in urls}

    (_results, _links, outcomes, attempted), slept = await _recover(monkeypatch, urls, scripted)

    assert attempted == 3, "stopped after the 3rd consecutive failure"
    assert len(slept) == 3, "no cooldown burned on the abandoned URLs"
    assert len(outcomes) == len(urls), "every URL still gets an honest outcome"
    assert all(o["reason_code"] == FetchReasonCode.HTTP_5XX.value for o in outcomes)


@pytest.mark.asyncio
async def test_recovery_success_resets_the_consecutive_failure_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fail, fail, success, fail, fail must NOT trip a breaker set at 3 —
    the intermittent-challenge case this recovery exists for."""
    monkeypatch.setattr(settings, "crawl_sequential_recovery_max_consecutive_failures", 3)
    urls = [f"https://example.com/p{i}" for i in range(5)]
    scripted = {
        urls[0]: _blocked_result(),
        urls[1]: _blocked_result(),
        urls[2]: _ok_result(urls[2]),
        urls[3]: _blocked_result(),
        urls[4]: _blocked_result(),
    }

    (results, _links, outcomes, attempted), _slept = await _recover(monkeypatch, urls, scripted)

    assert attempted == 5, "breaker must not fire; the run was never 3 in a row"
    assert [r.url for r in results] == [urls[2]]
    by_url = {o["url"]: o["reason_code"] for o in outcomes}
    assert by_url[urls[2]] == FetchReasonCode.SUCCESS.value


@pytest.mark.asyncio
async def test_recovery_stops_when_wall_clock_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One crawl job must not hold a worker slot indefinitely: 60 attempts
    x 75s would be 75 minutes without this bound."""
    monkeypatch.setattr(settings, "crawl_sequential_recovery_max_seconds", 100.0)
    monkeypatch.setattr(settings, "crawl_sequential_recovery_max_consecutive_failures", 99)

    ticks = iter([0.0, 40.0, 80.0, 120.0, 160.0, 200.0])
    monkeypatch.setattr(crawl4ai_client, "_recovery_monotonic", lambda: next(ticks))

    urls = [f"https://example.com/p{i}" for i in range(5)]
    scripted = {u: _ok_result(u) for u in urls}

    (_results, _links, outcomes, attempted), _slept = await _recover(monkeypatch, urls, scripted)

    assert attempted == 2, "stopped once the elapsed clock passed the budget"
    assert len(outcomes) == len(urls)
    assert outcomes[-1]["reason_code"] == FetchReasonCode.HTTP_5XX.value
