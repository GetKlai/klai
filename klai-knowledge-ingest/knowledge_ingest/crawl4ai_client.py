"""HTTP client for the Crawl4AI REST API (shared Docker container).

Replaces direct crawl4ai Python library usage.  All crawl requests go through
the REST API at ``settings.crawl4ai_api_url`` so knowledge-ingest does not need
the crawl4ai package (or a local Chromium install) as a dependency.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urldefrag, urlparse, urlunparse

import httpx
import structlog

from knowledge_ingest.config import settings
from knowledge_ingest.reason_codes import FetchReasonCode

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Shape of the per-URL outcome captured for crawl_jobs.fetch_outcomes JSONB.
# Keys match the migration shape: {"url", "reason_code", "status_code",
# "content_length"}. ``reason_code`` MUST be a FetchReasonCode value.
# ---------------------------------------------------------------------------

FetchOutcome = dict[str, Any]

# ---------------------------------------------------------------------------
# JS scripts — single source of truth for content filtering
# ---------------------------------------------------------------------------

# Injected BEFORE wait_for: strip nav chrome so the word-count condition fires
# only when article content is present.  Uses semantic selectors only — never
# class/id substring selectors (see pitfalls/backend.md).
JS_REMOVE_CHROME = """
[
  'nav', 'header', 'footer', 'aside',
  '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]', '[role="complementary"]',
  '[role="search"]',
].forEach(sel => document.querySelectorAll(sel).forEach(el => el.remove()));
"""

# Injected AFTER wait_for: open collapsed toggles (Notion / <details>).
JS_EXPAND_TOGGLES = """
document.querySelectorAll('details:not([open])').forEach(d => d.setAttribute('open', ''));
document.querySelectorAll('.notion-toggle__summary, [data-block-type="toggle"] > *:first-child').forEach(s => s.click());
await new Promise(r => setTimeout(r, 300));
"""

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CrawlResult:
    """Normalised result from a single-page crawl."""

    url: str
    fit_markdown: str
    raw_markdown: str
    html: str
    word_count: int
    success: bool
    links: dict[str, list[dict]] = field(default_factory=dict)
    # SPEC-CRAWLER-004 Fase A: crawl4ai populates ``media.images`` with dicts
    # shaped like ``{"src": "...", "alt": "...", "score": N}``. Other keys
    # (``videos``, ``audios``) exist but knowledge-ingest currently ignores them.
    media: dict[str, list[dict]] = field(default_factory=dict)
    error_message: str | None = None
    metadata: dict[str, Any] | None = None
    response_headers: dict[str, str] | None = None


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------


def build_crawl_config(
    selector: str | None,
    login_indicator_selector: str | None = None,
) -> dict[str, Any]:
    """Build a CrawlerRunConfig-compatible JSON payload.

    Pipeline switching (SPEC-CRAWL-001 / R-1):
    - No selector  → full pipeline (JS chrome removal, excluded_tags, PruningContentFilter)
    - Selector      → trusted pipeline (no JS removal, no excluded_tags, PruningContentFilter)

    Login indicator (SPEC-CRAWLER-004 Fase B / REQ-02.3):
    - When *login_indicator_selector* is set, the caller's base ``wait_for``
      is negated with ``&& !document.querySelector('<selector>')``. If the
      selector matches, the page never satisfies ``wait_for`` and crawl4ai
      returns ``success=False`` after ``page_timeout``. The caller can then
      treat that failure as an auth-wall event.
    """
    md_gen: dict[str, Any] = {
        "type": "DefaultMarkdownGenerator",
        "params": {
            "content_filter": {
                "type": "PruningContentFilter",
                "params": {"threshold": 0.45, "threshold_type": "dynamic"},
            },
            "options": {"type": "dict", "value": {"ignore_links": False, "body_width": 0}},
        },
    }

    base_wait = "js:() => document.body.innerText.trim().split(/\\s+/).length > 50"
    if login_indicator_selector:
        # Escape quotes/backslashes to prevent JS injection from a stored selector.
        selector_escaped = login_indicator_selector.replace("\\", "\\\\").replace("'", "\\'")
        # Negate: page is only "ready" when base condition is met AND the
        # login indicator is NOT present. When the indicator IS present the
        # wait_for times out and crawl4ai returns success=False.
        base_wait = (
            "js:() => (document.body.innerText.trim().split(/\\s+/).length > 50) "
            f"&& !document.querySelector('{selector_escaped}')"
        )

    params: dict[str, Any] = {
        "cache_mode": "bypass",
        "word_count_threshold": 10,
        "wait_for": base_wait,
        "js_code": JS_EXPAND_TOGGLES,
        "remove_consent_popups": True,
        "remove_overlay_elements": True,
        "page_timeout": 30000,
        "markdown_generator": md_gen,
    }

    if selector:
        # Use target_elements instead of css_selector so BFS link discovery still
        # sees the full page DOM. css_selector shrinks the raw HTML before any
        # processing, which also hides sidebar/nav links from BFS — breaking
        # site crawls on wikis where the main <nav> holds all category links
        # (e.g. wiki.redcactus.cloud /nl/ pages = 1 instead of 30+).
        # target_elements only narrows the markdown/extraction pass; the BFS
        # strategy still discovers links from the full HTML.
        params["target_elements"] = [selector]
        params["excluded_tags"] = []
    else:
        params["js_code_before_wait"] = JS_REMOVE_CHROME
        params["excluded_tags"] = ["nav", "footer", "header", "aside", "script", "style"]

    return params


# ---------------------------------------------------------------------------
# REST API helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    if settings.crawl4ai_api_key:
        return {"Authorization": f"Bearer {settings.crawl4ai_api_key}"}
    return {}


async def _fetch_sitemap_urls(base_url: str) -> list[str]:
    """Fetch same-domain URLs from sitemap.xml.

    Best-effort — returns [] on any error (sitemap is optional). The
    caller decides whether absent sitemap is fatal or fallback-worthy
    (SPEC-INGEST-RECONCILE-001 AC-3 requires fallback to BFS-only,
    not crawl failure).
    """
    sitemap_url = base_url.rstrip("/") + "/sitemap.xml"
    base_domain = urlparse(base_url).netloc.lower()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(sitemap_url, headers=_auth_headers())
            resp.raise_for_status()
            locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", resp.text)
            return [u for u in locs if urlparse(u).netloc.lower() == base_domain]
    except Exception as exc:
        # AC-3: log unavailability at warning level; caller falls back.
        logger.warning(
            "crawl_discovery_sitemap_unavailable",
            sitemap_url=sitemap_url,
            error=str(exc),
        )
        return []


# SPEC-INGEST-RECONCILE-001 — URL canonicalisation for set-union dedup.
# Trailing slash + fragment + scheme/host casing are common false-negatives
# in the previous "exact-string match" supplement loop dedup (Bug A defect 3).
def _canonicalise_url(url: str) -> str:
    """Normalise a URL for set-union dedup.

    - Lowercase scheme + host
    - Strip URL fragment (#section)
    - Strip trailing slash from path (but keep root "/" as-is)

    Query string is preserved — different ?foo=bar variants are
    different pages by convention.
    """
    if not url:
        return url
    defragged, _ = urldefrag(url)
    parsed = urlparse(defragged)
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            parsed.params,
            parsed.query,
            "",  # fragment
        )
    )


def _classify_fetch_outcome(
    page_result: dict[str, Any] | None,
    *,
    error: BaseException | None = None,
) -> str:
    """Map a crawl4ai per-URL result (or transport exception) to FetchReasonCode.

    Stable mapping — used to populate ``crawl_jobs.fetch_outcomes`` JSONB
    keyed by FetchReasonCode value (AC-4, AC-11). New cases here MUST
    correspond to an existing enum member; if none fits, add one to
    ``reason_codes.py`` AND extend the migration's allowed set first.
    """
    if error is not None:
        msg = str(error).lower()
        if isinstance(error, httpx.TimeoutException) or "timeout" in msg:
            return FetchReasonCode.TIMEOUT.value
        if isinstance(error, httpx.ConnectError) or "connection" in msg:
            return FetchReasonCode.CONNECTION_ERROR.value
        if "name or service not known" in msg or "dns" in msg or "nodename" in msg:
            return FetchReasonCode.DNS_ERROR.value
        return FetchReasonCode.UNKNOWN_EXCEPTION.value

    if not page_result:
        return FetchReasonCode.UNKNOWN_EXCEPTION.value

    if page_result.get("success"):
        return FetchReasonCode.SUCCESS.value

    status = page_result.get("status_code")
    err_msg = (page_result.get("error_message") or "").lower()

    if status == 429:
        return FetchReasonCode.RATE_LIMITED.value
    if status in (401, 403):
        return FetchReasonCode.AUTH_ERROR.value
    if isinstance(status, int) and 400 <= status < 500:
        return FetchReasonCode.HTTP_4XX.value
    if isinstance(status, int) and 500 <= status < 600:
        return FetchReasonCode.HTTP_5XX.value

    if "timeout" in err_msg:
        return FetchReasonCode.TIMEOUT.value
    if "dns" in err_msg or "name or service not known" in err_msg:
        return FetchReasonCode.DNS_ERROR.value
    if "connection" in err_msg:
        return FetchReasonCode.CONNECTION_ERROR.value
    if "parse" in err_msg or "decode" in err_msg or "html" in err_msg:
        return FetchReasonCode.PARSE_ERROR.value

    return FetchReasonCode.UNKNOWN_EXCEPTION.value


async def _crawl_sync(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Submit a crawl to POST /crawl and return the synchronous response.

    POST /crawl is a synchronous endpoint — it blocks until crawling is
    complete and returns results directly (no task_id, no polling needed).
    """
    resp = await client.post(
        f"{settings.crawl4ai_api_url}/crawl",
        json=payload,
        headers=_auth_headers(),
    )
    resp.raise_for_status()
    return resp.json()


def _extract_result(url: str, page: dict[str, Any]) -> CrawlResult:
    """Parse a single page result from the REST API response."""
    md = page.get("markdown", "")
    if isinstance(md, dict):
        fit = md.get("fit_markdown", "") or ""
        raw = md.get("raw_markdown", "") or ""
    else:
        fit = ""
        raw = md or ""

    md_v2 = page.get("markdown_v2", {})
    if not fit:
        fit = md_v2.get("fit_markdown", "") or ""
    if not raw:
        raw = md_v2.get("raw_markdown", "") or ""

    text = fit or raw
    return CrawlResult(
        url=page.get("url", url),
        fit_markdown=fit,
        raw_markdown=raw,
        html=page.get("html", ""),
        word_count=len(text.split()),
        success=page.get("success", True),
        links=page.get("links", {}),
        media=page.get("media") or {},
        error_message=page.get("error_message"),
        metadata=page.get("metadata"),
        response_headers=page.get("response_headers"),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _build_cookie_hooks(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    """Build Crawl4AI hooks payload that injects cookies via on_page_context_created."""
    cookies_json = json.dumps(cookies)
    hook_code = f"""
async def hook(page, context, **kwargs):
    await context.add_cookies({cookies_json})
    return page
"""
    return {"code": {"on_page_context_created": hook_code}, "timeout": 30}


async def crawl_page(
    url: str,
    selector: str | None = None,
    cookies: list[dict[str, Any]] | None = None,
) -> CrawlResult:
    """Crawl a single page via the Crawl4AI REST API.

    Uses the same pipeline switching as klai-connector (SPEC-CRAWL-001).
    When cookies are provided, they are injected into the browser context
    before the page loads via the on_page_context_created hook.
    """
    config = build_crawl_config(selector)
    payload: dict[str, Any] = {
        "urls": [url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": config},
    }
    if cookies:
        payload["hooks"] = _build_cookie_hooks(cookies)

    # DIAG-COOKIE-BUG-2026-05-07 — temporary diagnostic for connector wizard
    # cookie pass-through audit. Logs cookie NAMES and short value PREFIXES
    # (8 chars, ~6 bits of entropy) so we can correlate which cookie shape
    # the frontend sent vs what arrives at crawl4ai. NEVER log full values.
    # Remove after fix is verified in production.
    if cookies:
        cookie_names = [c.get("name", "<missing>") for c in cookies]
        cookie_value_prefixes = [
            (c.get("value", "")[:8] + "..." if c.get("value") else "<empty>") for c in cookies
        ]
        logger.info(
            "crawl_page_cookies_attached",
            url=url,
            cookie_count=len(cookies),
            cookie_names=cookie_names,
            cookie_value_prefixes=cookie_value_prefixes,
        )
    else:
        logger.info("crawl_page_cookies_absent", url=url)

    async with httpx.AsyncClient(timeout=90.0) as client:
        try:
            data = await _crawl_sync(client, payload)
        except Exception as exc:
            logger.warning("crawl4ai_request_failed", url=url, error=str(exc))
            return CrawlResult(
                url=url,
                fit_markdown="",
                raw_markdown="",
                html="",
                word_count=0,
                success=False,
                error_message=str(exc),
            )

    results = data.get("results", [])
    if isinstance(results, dict):
        results = [results]

    if not results:
        return CrawlResult(
            url=url,
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="No results returned",
        )

    result = _extract_result(url, results[0])
    logger.info(
        "crawl4ai_page_result",
        url=url,
        selector=selector,
        fit_words=len(result.fit_markdown.split()),
        raw_words=len(result.raw_markdown.split()),
    )
    return result


async def crawl_site(
    start_url: str,
    selector: str | None = None,
    max_depth: int = 2,
    max_pages: int = 200,
    include_patterns: list[str] | None = None,
    login_indicator_selector: str | None = None,
    cookies: list[dict[str, Any]] | None = None,
) -> tuple[list[CrawlResult], list[FetchOutcome]]:
    """Crawl a site via server-side BFS deep crawl + sitemap orphan supplement.

    Two-phase architecture:

    1. **Phase 1 — Server-side recursive BFS** (``/crawl/job`` +
       ``BFSDeepCrawlStrategy``): crawl4ai walks the site link-graph
       breadth-first up to ``max_depth`` levels and ``max_pages`` total,
       respecting ``include_patterns`` via ``URLPatternFilter`` (real
       fnmatch glob, not the substring approximation). crawl4ai's own
       MemoryAdaptiveDispatcher handles concurrency safely server-side
       — no client-side ``asyncio.gather`` over a shared connection.

    2. **Phase 2 — Sitemap supplement** (chunked ``/crawl``): URLs in
       ``sitemap.xml`` that the BFS did not visit (orphans, pages not
       reachable via internal links from the start_url) are submitted via
       the chunked-bulk-fetch path (≤100 URLs/request — crawl4ai 0.8
       enforces that cap on the ``urls`` array). Per-chunk transport
       failure logged but does not abort remaining chunks.

    Phase 1 alone covers wikis/docs (recursive link-following). Phase 2
    catches orphans (sitemap-only entries the homepage doesn't link to).
    Together they reach the BOTH "everything in sitemap" AND "everything
    reachable by following links" — a property neither alone provides.

    Why two phases (and why the OLD pre-RECONCILE pattern was right):

    - help.voys.nl: 208 sitemap entries, BFS-reachable subset is smaller →
      Phase 1 covers most, Phase 2 fills the orphans.
    - wiki.redcactus.cloud: no sitemap, ~150-300 internally-linked pages →
      Phase 1 covers everything, Phase 2 is a no-op.

    SPEC-INGEST-RECONCILE-001 replaced this pattern with "discovery
    upfront, no recursion" because the old Phase 2 used
    ``asyncio.gather(*[crawl_page(u)…], return_exceptions=True)`` which
    silently dropped pages on concurrent-conn errors. THAT was the bug.
    Phase 1 (server-side BFS) was always correct. The fix is to keep
    Phase 1 and replace Phase 2's gather with the chunked-bulk-fetch
    contract (which serialises work through crawl4ai's own dispatcher).

    Returns ``(crawl_results, outcomes)``:
    - ``crawl_results``: same-domain pages with non-empty markdown.
    - ``outcomes``: per-URL ``{"url", "reason_code", "status_code",
      "content_length"}`` records written to ``crawl_jobs.fetch_outcomes``
      JSONB so operators can answer "where did the missing pages go?"
      without log forensics (RECONCILE's good addition, retained).
    """
    parsed = urlparse(start_url)
    base_domain = parsed.netloc.lower()
    crawler_config = build_crawl_config(
        selector,
        login_indicator_selector=login_indicator_selector,
    )

    # ------------------------------------------------------------------
    # Phase 1 — Server-side BFS deep crawl via crawl4ai BFSDeepCrawlStrategy.
    # ------------------------------------------------------------------
    bfs_results, bfs_error = await _bfs_deep_crawl(
        start_url=start_url,
        crawler_config=crawler_config,
        max_depth=max_depth,
        max_pages=max_pages,
        include_patterns=include_patterns,
        cookies=cookies,
    )
    # Same-domain guard: crawl4ai may follow external links if filter_chain
    # doesn't catch them (e.g. unconfigured include_patterns + permissive
    # discovery). Drop external pages here so caller doesn't ingest them.
    bfs_results = [r for r in bfs_results if urlparse(r.url).netloc.lower() == base_domain]

    # ------------------------------------------------------------------
    # Phase 2 — Sitemap supplement: pages in sitemap.xml that BFS missed
    # (orphans not reachable via internal links from start_url).
    # ------------------------------------------------------------------
    sitemap_urls = await _fetch_sitemap_urls(start_url)
    seen_canonicals: set[str] = {_canonicalise_url(start_url)}
    seen_canonicals.update(_canonicalise_url(r.url) for r in bfs_results)

    supplement_candidates: list[str] = []
    remaining_budget = max_pages - len(bfs_results)
    for u in sitemap_urls:
        if remaining_budget <= 0:
            break
        canonical = _canonicalise_url(u)
        if canonical in seen_canonicals:
            continue
        if not _url_matches_include_patterns(u, include_patterns):
            continue
        if urlparse(u).netloc.lower() != base_domain:
            continue
        seen_canonicals.add(canonical)
        supplement_candidates.append(u)
        remaining_budget -= 1

    logger.info(
        "crawl_site_discovery_complete",
        start_url=start_url,
        bfs_pages=len(bfs_results),
        bfs_error=str(bfs_error) if bfs_error else None,
        sitemap_urls=len(sitemap_urls),
        supplement_candidates=len(supplement_candidates),
        max_pages=max_pages,
    )

    supplement_raw_results, supplement_transport_error = await _chunked_bulk_fetch(
        urls=supplement_candidates,
        crawler_config=crawler_config,
        cookies=cookies,
    )

    # ------------------------------------------------------------------
    # Combine BFS results + supplement results into the
    # (crawl_results, outcomes) contract.
    # ------------------------------------------------------------------
    crawl_results: list[CrawlResult] = []
    outcomes: list[FetchOutcome] = []

    # BFS results: each visited page becomes one outcome + one ingestable
    # result (when same-domain + non-empty markdown).
    for r in bfs_results:
        outcomes.append(_build_outcome_from_result(r.url, r))
        if _result_is_ingestable(r, base_domain=base_domain):
            crawl_results.append(r)

    # If the BFS itself failed (network/timeout/5xx) AND we got no results,
    # surface a transport_error outcome for start_url so operators see the
    # failure in fetch_outcomes instead of a silent empty BFS.
    if bfs_error is not None and not bfs_results:
        outcomes.append(
            {
                "url": start_url,
                "reason_code": FetchReasonCode.UNKNOWN_EXCEPTION.value,
                "status_code": None,
                "content_length": 0,
            }
        )

    # Supplement results: canonical-URL matched against the chunked-bulk
    # response, with positional fallback for redirect cases (matches the
    # RECONCILE contract).
    supplement_results, supplement_outcomes = _combine_bulk_responses(
        candidates=supplement_candidates,
        raw_results=supplement_raw_results,
        transport_error=supplement_transport_error,
        base_domain=base_domain,
    )
    crawl_results.extend(supplement_results)
    outcomes.extend(supplement_outcomes)

    success_count = sum(1 for o in outcomes if o["reason_code"] == FetchReasonCode.SUCCESS.value)
    logger.info(
        "crawl_site_complete",
        start_url=start_url,
        candidates=len(outcomes),
        results=len(crawl_results),
        success_outcomes=success_count,
        non_success_outcomes=len(outcomes) - success_count,
        bfs_pages=len(bfs_results),
        supplement_pages=len(supplement_results),
    )

    return crawl_results, outcomes


# ---------------------------------------------------------------------------
# crawl_site internals — kept module-private for testability.
# ---------------------------------------------------------------------------


async def _fetch_seed_page(
    *,
    start_url: str,
    crawler_config: dict[str, Any],
    cookies: list[dict[str, Any]] | None,
) -> CrawlResult:
    """Fetch ``start_url`` once with the SAME config the bulk path uses.

    Distinct from ``crawl_page`` because crawl_page rebuilds config from
    ``selector`` only — it has no knob for ``login_indicator_selector``,
    so reusing it for the seed would silently strip auth-wall detection
    and let the seed succeed on a login page (extracting login-form
    anchors as BFS seeds, then auth-failing every URL in the bulk).
    """
    payload: dict[str, Any] = {
        "urls": [start_url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": crawler_config},
    }
    if cookies:
        payload["hooks"] = _build_cookie_hooks(cookies)

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            data = await _crawl_sync(client, payload)
    except Exception as exc:
        logger.warning(
            "crawl_site_seed_request_failed",
            start_url=start_url,
            error=str(exc),
        )
        return CrawlResult(
            url=start_url,
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message=str(exc),
        )

    pages = _normalise_results_block(data)
    if not pages:
        logger.warning("crawl_site_seed_empty_response", start_url=start_url)
        return CrawlResult(
            url=start_url,
            fit_markdown="",
            raw_markdown="",
            html="",
            word_count=0,
            success=False,
            error_message="empty response",
        )

    return _extract_result(start_url, pages[0])


def _extract_bfs_seeds(seed_result: CrawlResult, *, base_domain: str) -> list[str]:
    """Extract same-domain internal links from the seed page.

    Returns an empty list when the seed itself failed (auth wall, 5xx,
    etc.) — the candidate set then degrades cleanly to sitemap-only.
    """
    if not seed_result.success:
        return []
    internal = (seed_result.links or {}).get("internal") or []
    seeds: list[str] = []
    for entry in internal:
        href = entry.get("href") if isinstance(entry, dict) else None
        if not href:
            continue
        if urlparse(href).netloc.lower() != base_domain:
            continue
        seeds.append(href)
    return seeds


def _build_outcome_from_result(url: str, result: CrawlResult) -> FetchOutcome:
    """Map a CrawlResult (the seed path) to a fetch_outcomes JSONB entry."""
    if result.success:
        reason_code = FetchReasonCode.SUCCESS.value
    else:
        # Synthesise a page-shape dict for the classifier so the same
        # error_message → FetchReasonCode mapping applies as for the
        # bulk path.
        reason_code = _classify_fetch_outcome(
            {
                "success": False,
                "status_code": None,
                "error_message": result.error_message or "",
            },
        )
    return {
        "url": url,
        "reason_code": reason_code,
        "status_code": None,
        "content_length": len(result.html or ""),
    }


def _result_is_ingestable(result: CrawlResult, *, base_domain: str) -> bool:
    """Same-domain + non-empty + success — the legacy ingest-loop contract."""
    if not result.success:
        return False
    if not (result.fit_markdown or result.raw_markdown):
        return False
    return urlparse(result.url).netloc.lower() == base_domain


def _combine_bulk_responses(
    *,
    candidates: list[str],
    raw_results: list[dict[str, Any]],
    transport_error: BaseException | None,
    base_domain: str,
) -> tuple[list[CrawlResult], list[FetchOutcome]]:
    """Match bulk candidates to crawl4ai responses; produce results + outcomes.

    Matching uses canonical-URL lookup first. For unmatched candidates we
    fall back to positional alignment with the response list — crawl4ai's
    bulk endpoint preserves submission order, and a redirect (``/old-page``
    → ``/new-page``) is the only common reason for a candidate's canonical
    to disappear from the response set. The fallback only claims a
    positional response that has not already been canonical-matched to a
    different candidate, so a redirect doesn't silently shadow a legitimate
    direct hit.
    """
    crawl_results: list[CrawlResult] = []
    outcomes: list[FetchOutcome] = []

    if not candidates:
        return crawl_results, outcomes

    # Canonical-URL → response, populated from the bulk response body.
    by_canonical: dict[str, dict[str, Any]] = {}
    for page in raw_results:
        if not page:
            continue
        result_url = page.get("url") or ""
        if not result_url:
            continue
        by_canonical[_canonicalise_url(result_url)] = page

    candidate_canonicals = [_canonicalise_url(c) for c in candidates]
    canonical_to_idx: dict[str, int] = {c: i for i, c in enumerate(candidate_canonicals)}
    # Track which positional response has already been claimed by canonical
    # match. Prevents the redirect-fallback from re-claiming.
    claimed_response_indices: set[int] = set()
    for canonical in candidate_canonicals:
        page = by_canonical.get(canonical)
        if page is None:
            continue
        # Locate the positional index of this matched response so the
        # redirect fallback won't claim it again.
        for j, raw in enumerate(raw_results):
            if raw is page:
                claimed_response_indices.add(j)
                break

    for i, url in enumerate(candidates):
        if transport_error is not None:
            outcomes.append(
                {
                    "url": url,
                    "reason_code": _classify_fetch_outcome(None, error=transport_error),
                    "status_code": None,
                    "content_length": 0,
                }
            )
            continue

        page = by_canonical.get(candidate_canonicals[i])

        # Redirect fallback: positional alignment when canonical match
        # missed AND the response at this index hasn't been claimed AND
        # its URL doesn't canonical-match a DIFFERENT candidate (which
        # would be ambiguous) AND the response stays on the same origin
        # domain. The same-domain guard makes the fallback safe even if
        # crawl4ai's MemoryAdaptiveDispatcher reorders the response list
        # under load — at worst we miss a redirect classification and
        # surface UNKNOWN_EXCEPTION (visible to operators) rather than
        # silently mislabel an unrelated URL.
        if (
            page is None
            and len(raw_results) == len(candidates)
            and i < len(raw_results)
            and i not in claimed_response_indices
        ):
            positional = raw_results[i]
            if positional and positional.get("url"):
                pos_url = positional["url"]
                pos_canonical = _canonicalise_url(pos_url)
                pos_owner_idx = canonical_to_idx.get(pos_canonical)
                pos_domain = urlparse(pos_url).netloc.lower()
                if (pos_owner_idx is None or pos_owner_idx == i) and pos_domain == base_domain:
                    page = positional
                    claimed_response_indices.add(i)

        outcomes.append(
            {
                "url": url,
                "reason_code": _classify_fetch_outcome(page),
                "status_code": (page or {}).get("status_code"),
                "content_length": len((page or {}).get("html", "") or ""),
            }
        )

        if page is None:
            continue

        result = _extract_result(url, page)
        if _result_is_ingestable(result, base_domain=base_domain):
            crawl_results.append(result)

    return crawl_results, outcomes


# Single bulk-crawl request budget. crawl4ai's MemoryAdaptiveDispatcher
# handles in-batch concurrency server-side; the client just waits for
# the whole batch. Voys-scale Voys/support (~500 candidates) completes
# well under 90s on the production container — tuned to give 5x headroom.
_BULK_CRAWL_TIMEOUT = 5 * 60.0

# crawl4ai 0.8 server enforces ``List should have at most 100 items after
# validation`` on POST /crawl ``urls``. The constant lives here so it can
# be raised in lock-step with any future crawl4ai schema relaxation.
# Discovered live on help.voys.nl (208 sitemap entries → 422 → 1 ingested).
_BULK_CHUNK_SIZE = 100

# Server-side BFS deep crawl polling budget. /crawl/job is async — submit,
# get task_id, poll status. Voys-support full-depth crawl (~500 pages
# across 3 levels) completes well under 30 minutes; the cap is a safety
# net for stuck workers.
_DEEP_POLL_INTERVAL = 5.0  # seconds between status polls
_MAX_DEEP_POLL = 30 * 60  # max total seconds (30 minutes)


def _url_matches_include_patterns(u: str, include_patterns: list[str] | None) -> bool:
    """Filter URL against include_patterns using crawl4ai URLPatternFilter semantics.

    A pattern like ``/nl/*`` is a prefix glob (fnmatch on the URL path), NOT a
    substring. Plain substring match made ``/nl/*`` literally never match
    because no URL contains the asterisk — observed live on
    wiki.redcactus.cloud (29 BFS-discovered URLs, 0 retained as candidates →
    1 page ingested instead of ~150). Two contracts honoured:

      * Patterns containing ``*`` or ``?`` ⇒ ``fnmatch`` against the URL path
        (matches the old crawl4ai URLPatternFilter contract)
      * Plain patterns (no wildcard) ⇒ substring match on the URL —
        matches the original "simple substring" intent for legacy callers
    """
    if not include_patterns:
        return True
    path_with_query = urlparse(u).path
    for p in include_patterns:
        if "*" in p or "?" in p:
            if fnmatch.fnmatch(path_with_query, p):
                return True
        elif p in u:
            return True
    return False


async def _bfs_deep_crawl(
    *,
    start_url: str,
    crawler_config: dict[str, Any],
    max_depth: int,
    max_pages: int,
    include_patterns: list[str] | None,
    cookies: list[dict[str, Any]] | None,
) -> tuple[list[CrawlResult], BaseException | None]:
    """Server-side BFS deep crawl via crawl4ai's ``/crawl/job`` endpoint.

    Submits a single crawl job that uses crawl4ai's ``BFSDeepCrawlStrategy``
    (recursive multi-level link-following) with optional ``URLPatternFilter``
    (real fnmatch glob, not the substring approximation). crawl4ai's own
    server-side ``MemoryAdaptiveDispatcher`` handles concurrency safely —
    no client-side ``asyncio.gather`` over a shared connection.

    Returns ``(results, transport_error)``:
      * ``results`` — list of ``CrawlResult`` for every URL the BFS visited
        (including the start_url itself; results may exceed ``max_pages``
        slightly because crawl4ai counts queued pages, not strictly
        finished ones).
      * ``transport_error`` — non-None when the submission/polling itself
        failed (network error, timeout, 5xx). ``results`` is empty on
        failure; the caller decides whether to fall back to sitemap-only
        or surface as an error.
    """
    deep_crawl_params: dict[str, Any] = {
        "max_depth": max_depth,
        "max_pages": max_pages,
        "include_external": False,
    }
    if include_patterns:
        # Crawl4AI 0.8.6 only reconstructs nested objects when wrapped in
        # ``{"type": "<ClassName>", "params": {...}}`` — a bare list stays a
        # list and BFSDeepCrawlStrategy crashes with
        # ``AttributeError: 'list' object has no attribute 'apply'`` the
        # moment it walks past depth 0. Pinned by tests.
        deep_crawl_params["filter_chain"] = {
            "type": "FilterChain",
            "params": {
                "filters": [
                    {
                        "type": "URLPatternFilter",
                        "params": {"patterns": include_patterns},
                    },
                ],
            },
        }

    config = dict(crawler_config)
    config["deep_crawl_strategy"] = {
        "type": "BFSDeepCrawlStrategy",
        "params": deep_crawl_params,
    }

    payload: dict[str, Any] = {
        "urls": [start_url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": config},
    }
    if cookies:
        payload["hooks"] = _build_cookie_hooks(cookies)

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{settings.crawl4ai_api_url}/crawl/job",
                json=payload,
                headers=_auth_headers(),
            )
            resp.raise_for_status()
            task_id: str = resp.json()["task_id"]
            logger.info(
                "crawl_site_bfs_job_submitted",
                start_url=start_url,
                task_id=task_id,
                max_depth=max_depth,
                max_pages=max_pages,
            )

            elapsed = 0.0
            result_data: dict[str, Any] = {}
            while elapsed < _MAX_DEEP_POLL:
                await asyncio.sleep(_DEEP_POLL_INTERVAL)
                elapsed += _DEEP_POLL_INTERVAL
                resp = await client.get(
                    f"{settings.crawl4ai_api_url}/crawl/job/{task_id}",
                    headers=_auth_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                status_str = data.get("status", "").lower()
                if status_str == "completed":
                    result_data = data.get("result", {}) or {}
                    break
                if status_str == "failed":
                    err_msg = data.get("error", "unknown")
                    raise RuntimeError(f"BFS deep crawl job {task_id} failed: {err_msg}")
            else:
                raise TimeoutError(
                    f"BFS deep crawl job {task_id} did not complete within {_MAX_DEEP_POLL}s"
                )
    except Exception as exc:
        logger.warning(
            "crawl_site_bfs_failed",
            start_url=start_url,
            error=str(exc),
        )
        return [], exc

    raw_results = _normalise_results_block(result_data)
    crawl_results = [_extract_result(start_url, page) for page in raw_results if page]
    logger.info(
        "crawl_site_bfs_complete",
        start_url=start_url,
        pages=len(crawl_results),
    )
    return crawl_results, None


async def _chunked_bulk_fetch(
    *,
    urls: list[str],
    crawler_config: dict[str, Any],
    cookies: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], BaseException | None]:
    """Submit ``urls`` to crawl4ai's bulk ``/crawl`` endpoint in chunks of 100.

    crawl4ai 0.8 server enforces a 100-URL cap on the ``urls`` array.
    Submitting more in one request returns 422. We chunk client-side and
    accumulate raw results; per-chunk transport failure is logged but the
    remaining chunks still ship — partial coverage beats zero coverage.
    """
    raw_results: list[dict[str, Any]] = []
    transport_error: BaseException | None = None
    if not urls:
        return raw_results, None
    for chunk_start in range(0, len(urls), _BULK_CHUNK_SIZE):
        chunk_urls = urls[chunk_start : chunk_start + _BULK_CHUNK_SIZE]
        payload: dict[str, Any] = {
            "urls": chunk_urls,
            "crawler_config": {"type": "CrawlerRunConfig", "params": crawler_config},
        }
        if cookies:
            payload["hooks"] = _build_cookie_hooks(cookies)
        try:
            async with httpx.AsyncClient(timeout=_BULK_CRAWL_TIMEOUT) as client:
                data = await _crawl_sync(client, payload)
            raw_results.extend(_normalise_results_block(data))
        except Exception as exc:
            transport_error = exc
            logger.warning(
                "crawl_site_bulk_chunk_failed",
                chunk_index=chunk_start // _BULK_CHUNK_SIZE,
                chunk_size=len(chunk_urls),
                error=str(exc),
            )
    return raw_results, transport_error


def _normalise_results_block(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the various shapes crawl4ai returns into a list of dicts."""
    results = data.get("results", data.get("result", []))
    if isinstance(results, dict):
        return [results]
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict)]
    return []


def _build_candidate_set(
    *,
    start_url: str,
    sitemap_urls: list[str],
    bfs_seed_urls: list[str],
    base_domain: str,
    max_pages: int,
    include_patterns: list[str] | None,
    include_start_url: bool = True,
) -> list[str]:
    """Union(sitemap, BFS-seeds), filtered to same-domain, deduped, capped.

    AC-2: when union exceeds ``max_pages``, sitemap entries take priority
    over BFS-discovered URLs. ``include_patterns`` (when set) restricts
    candidates by simple substring match on the path — same semantics as
    the old crawl4ai URLPatternFilter without the deserialisation
    fragility (SPEC-CRAWL-001 v0.8.6 issue).

    ``include_start_url`` controls whether ``start_url`` is added as the
    first candidate. ``crawl_site`` sets it to ``False`` because the seed
    is fetched separately (with full config including login_indicator) and
    re-submitting it in the bulk would be a redundant fetch. Even when
    excluded, ``start_url``'s canonical form is recorded in the dedupe
    ``seen`` set so a sitemap entry that points back to the homepage
    doesn't sneak in as a duplicate.
    """

    def _same_domain(u: str) -> bool:
        return urlparse(u).netloc.lower() == base_domain

    def _matches_include(u: str) -> bool:
        # ``include_patterns`` carry crawl4ai URLPatternFilter semantics: a
        # value like ``/nl/*`` is a prefix glob, not a substring. Plain
        # substring match (``p in u``) makes ``/nl/*`` literally never match
        # because no URL contains the asterisk character — observed live on
        # wiki.redcactus.cloud (29 BFS-discovered URLs, 0 retained as
        # candidates → 1 page ingested instead of ~150). We honour both:
        #
        #   * Patterns containing ``*`` or ``?`` ⇒ ``fnmatch`` against the
        #     full URL path (incl. query). Same contract as the old crawl4ai
        #     URLPatternFilter.
        #   * Plain patterns (no wildcard) ⇒ substring match on the URL —
        #     matches the original "simple substring" intent.
        if not include_patterns:
            return True
        path_with_query = urlparse(u).path
        for p in include_patterns:
            if "*" in p or "?" in p:
                if fnmatch.fnmatch(path_with_query, p):
                    return True
            elif p in u:
                return True
        return False

    seen: set[str] = set()
    ordered: list[str] = []

    def _add(u: str) -> None:
        if not u:
            return
        if not _same_domain(u):
            return
        if not _matches_include(u):
            return
        canonical = _canonicalise_url(u)
        if canonical in seen:
            return
        seen.add(canonical)
        ordered.append(u)

    # Priority order: start_url first (when included), then sitemap (site-
    # owner truth), then BFS-discovered (best-effort).
    if include_start_url:
        _add(start_url)
    elif start_url:
        # Reserve start_url's canonical so dedupe excludes it without
        # adding it to the candidate list.
        seen.add(_canonicalise_url(start_url))
    for u in sitemap_urls:
        _add(u)
    for u in bfs_seed_urls:
        _add(u)

    if max_pages > 0 and len(ordered) > max_pages:
        ordered = ordered[:max_pages]
    return ordered


async def crawl_dom_summary(url: str) -> list[dict] | None:
    """Crawl a page with DOM extraction JS for AI selector detection.

    Injects JS that extracts a ranked DOM summary and captures it via
    a hidden <pre> element.
    """
    dom_js = """
(async () => {
  const els = [...document.body.querySelectorAll('*')]
    .filter(el => el.innerText && el.children.length < 5)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      className: (typeof el.className === 'string' ? el.className : null) || null,
      wordCount: el.innerText.trim().split(/\\s+/).length,
      selector: el.id
        ? '#' + el.id
        : (typeof el.className === 'string' && el.className.trim())
          ? el.tagName.toLowerCase() + '.' + el.className.trim().split(/\\s+/).join('.')
          : el.tagName.toLowerCase()
    }))
    .sort((a, b) => b.wordCount - a.wordCount)
    .slice(0, 25);

  const pre = document.createElement('pre');
  pre.id = '__klai_dom_summary__';
  pre.style.cssText = 'position:absolute;left:-9999px;top:-9999px;';
  pre.textContent = JSON.stringify(els);
  document.body.appendChild(pre);
})();
"""

    config: dict[str, Any] = {
        "cache_mode": "bypass",
        "js_code": dom_js,
        "css_selector": "#__klai_dom_summary__",
        "word_count_threshold": 0,
        "page_timeout": 30000,
        "remove_consent_popups": True,
    }
    payload = {
        "urls": [url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": config},
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            data = await _crawl_sync(client, payload)

        results = data.get("results", [])
        if isinstance(results, dict):
            results = [results]
        if not results:
            return None

        md = results[0].get("markdown", "")
        if isinstance(md, dict):
            raw = md.get("raw_markdown", "") or ""
        else:
            raw = md or ""

        raw = raw.strip()
        if not raw:
            return None
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("crawl4ai_dom_summary_failed", url=url, error=str(exc))
        return None
