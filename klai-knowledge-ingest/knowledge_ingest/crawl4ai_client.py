"""HTTP client for the Crawl4AI REST API (shared Docker container).

Replaces direct crawl4ai Python library usage.  All crawl requests go through
the REST API at ``settings.crawl4ai_api_url`` so knowledge-ingest does not need
the crawl4ai package (or a local Chromium install) as a dependency.
"""

from __future__ import annotations

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

    async with httpx.AsyncClient(timeout=90.0) as client:
        try:
            data = await _crawl_sync(client, payload)
        except Exception as exc:
            logger.warning("crawl4ai_request_failed", url=url, error=str(exc))
            return CrawlResult(
                url=url, fit_markdown="", raw_markdown="", html="",
                word_count=0, success=False, error_message=str(exc),
            )

    results = data.get("results", [])
    if isinstance(results, dict):
        results = [results]

    if not results:
        return CrawlResult(
            url=url, fit_markdown="", raw_markdown="", html="",
            word_count=0, success=False, error_message="No results returned",
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
    max_depth: int = 2,  # kept for caller-signature compat — see docstring
    max_pages: int = 200,
    include_patterns: list[str] | None = None,
    login_indicator_selector: str | None = None,
    cookies: list[dict[str, Any]] | None = None,
) -> tuple[list[CrawlResult], list[FetchOutcome]]:
    """Crawl a site via sitemap+BFS UNION submitted to crawl4ai's bulk /crawl.

    @MX:NOTE SPEC-INGEST-RECONCILE-001 Fix 1 — replaces the old BFS-with-
    post-supplement design that silently dropped sitemap URLs in an
    unbounded ``asyncio.gather`` loop (Bug A: 41/208 ingested for
    help.voys.nl). Discovery and fetch are now decoupled:

    1. **Discovery** — union(sitemap_xml_urls, BFS_seeds_from_homepage),
       canonicalised + deduped + capped at ``max_pages``. Sitemap takes
       priority on cap (AC-2: site-owner truth beats best-effort BFS).
       AC-3: missing sitemap → BFS-only fallback, never crawl failure.
    2. **Fetch** — single ``POST /crawl`` bulk call with the candidate
       URL list. crawl4ai's server-side ``MemoryAdaptiveDispatcher``
       handles concurrency.
    3. **Outcome capture** — every candidate URL produces one entry in
       the returned ``outcomes`` list (AC-4) keyed by ``FetchReasonCode``.

    Returns ``(crawl_results, outcomes)``:
    - ``crawl_results``: same-domain pages with non-empty markdown (the
      caller's old contract: pages worth ingesting).
    - ``outcomes``: per-URL records ``{"url", "reason_code", "status_code",
      "content_length"}`` — written to ``crawl_jobs.fetch_outcomes`` JSONB
      so operators can answer "where did the missing pages go?".

    ``max_depth`` is accepted for caller-signature compatibility but no
    longer drives discovery (we explicitly enumerate candidates instead
    of recursing). ``include_patterns``, when set, is applied as a
    candidate-side substring filter against the union before submission.
    """
    config = build_crawl_config(selector, login_indicator_selector=login_indicator_selector)
    parsed = urlparse(start_url)
    base_domain = parsed.netloc.lower()

    # ------------------------------------------------------------------
    # Discovery — sitemap + shallow BFS seed, union, dedup, cap.
    # ------------------------------------------------------------------
    sitemap_urls = await _fetch_sitemap_urls(start_url)

    bfs_seed_urls = await _bfs_discover_seed_urls(
        start_url=start_url,
        selector=selector,
        login_indicator_selector=login_indicator_selector,
        cookies=cookies,
    )

    candidates = _build_candidate_set(
        start_url=start_url,
        sitemap_urls=sitemap_urls,
        bfs_seed_urls=bfs_seed_urls,
        base_domain=base_domain,
        max_pages=max_pages,
        include_patterns=include_patterns,
    )

    logger.info(
        "crawl_site_discovery_complete",
        start_url=start_url,
        sitemap_urls=len(sitemap_urls),
        bfs_seed_urls=len(bfs_seed_urls),
        candidates=len(candidates),
        max_pages=max_pages,
    )

    if not candidates:
        logger.warning("crawl_site_no_candidates", start_url=start_url)
        return [], []

    # ------------------------------------------------------------------
    # Fetch — single bulk POST /crawl. Server-side dispatcher handles
    # concurrency; we get per-URL ``success`` + ``error_message`` in
    # the response body.
    # ------------------------------------------------------------------
    payload: dict[str, Any] = {
        "urls": candidates,
        "crawler_config": {"type": "CrawlerRunConfig", "params": config},
    }
    if cookies:
        payload["hooks"] = _build_cookie_hooks(cookies)

    raw_results: list[dict[str, Any]] = []
    transport_error: BaseException | None = None

    try:
        async with httpx.AsyncClient(timeout=_BULK_CRAWL_TIMEOUT) as client:
            data = await _crawl_sync(client, payload)
        raw_results = _normalise_results_block(data)
    except Exception as exc:
        # Whole-batch transport failure: every candidate gets the same
        # transport-classified outcome (AC-4: no candidate is "lost",
        # they all surface a reason). The caller's existing failure
        # path then reads the outcomes to decide.
        transport_error = exc
        logger.warning(
            "crawl_site_bulk_request_failed",
            start_url=start_url,
            candidates=len(candidates),
            error=str(exc),
        )

    # Map results back to candidates by URL (canonicalised). crawl4ai
    # keeps URL ordering but we don't rely on it.
    by_url: dict[str, dict[str, Any]] = {}
    for page in raw_results:
        if not page:
            continue
        result_url = page.get("url") or ""
        if not result_url:
            continue
        by_url[_canonicalise_url(result_url)] = page

    crawl_results: list[CrawlResult] = []
    outcomes: list[FetchOutcome] = []

    for url in candidates:
        canonical = _canonicalise_url(url)
        page = by_url.get(canonical)
        if transport_error is not None:
            reason_code = _classify_fetch_outcome(None, error=transport_error)
            outcomes.append(
                {
                    "url": url,
                    "reason_code": reason_code,
                    "status_code": None,
                    "content_length": 0,
                }
            )
            continue

        reason_code = _classify_fetch_outcome(page)
        content_length = len((page or {}).get("html", "") or "")
        outcomes.append(
            {
                "url": url,
                "reason_code": reason_code,
                "status_code": (page or {}).get("status_code"),
                "content_length": content_length,
            }
        )

        if page is None:
            # crawl4ai didn't return this URL — uncommon, but record it.
            continue

        result = _extract_result(url, page)
        # Same-domain filter as the legacy BFS path: external links
        # discovered via sitemap noise or rewrites stay out.
        if urlparse(result.url).netloc != parsed.netloc:
            continue
        # Only successful, non-empty pages reach the ingest loop. Failed
        # fetches stay in ``outcomes`` (where operators look for the
        # breakdown via ``crawl_jobs.fetch_outcomes``) so the caller's
        # ``pages_total`` reflects pages that actually have content. This
        # also preserves the legacy login_indicator semantics: a single
        # ``success=False`` result that the indicator caller sees triggers
        # ``AuthWallDetected`` — surfacing transient 5xx noise as
        # auth-walled would be a regression.
        if not result.success:
            continue
        if not (result.fit_markdown or result.raw_markdown):
            continue
        crawl_results.append(result)

    success_count = sum(1 for o in outcomes if o["reason_code"] == FetchReasonCode.SUCCESS.value)
    logger.info(
        "crawl_site_complete",
        start_url=start_url,
        candidates=len(candidates),
        results=len(crawl_results),
        success_outcomes=success_count,
        non_success_outcomes=len(outcomes) - success_count,
    )

    return crawl_results, outcomes


# Single bulk-crawl request budget. crawl4ai's MemoryAdaptiveDispatcher
# handles in-batch concurrency server-side; the client just waits for
# the whole batch. Voys-scale Voys/support (~500 candidates) completes
# well under 90s on the production container — tuned to give 5x headroom.
_BULK_CRAWL_TIMEOUT = 5 * 60.0


def _normalise_results_block(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the various shapes crawl4ai returns into a list of dicts."""
    results = data.get("results", data.get("result", []))
    if isinstance(results, dict):
        return [results]
    if isinstance(results, list):
        return [r for r in results if isinstance(r, dict)]
    return []


async def _bfs_discover_seed_urls(
    *,
    start_url: str,
    selector: str | None,
    login_indicator_selector: str | None,
    cookies: list[dict[str, Any]] | None,
) -> list[str]:
    """Shallow BFS — fetch start_url once, return its same-domain internal links.

    @MX:NOTE Replaces the previous deep-BFS via ``/crawl/job``. We only
    need the homepage's link set as the BFS contribution to the union;
    the rest of crawl4ai's BFS (recursive expansion) is the layer that
    duplicated work in the old design and produced no coverage signal.
    """
    base_domain = urlparse(start_url).netloc.lower()

    seed_result = await crawl_page(
        start_url,
        selector=selector,
        cookies=cookies,
    )
    if not seed_result.success:
        if login_indicator_selector:
            logger.warning(
                "crawl_site_seed_login_indicator_triggered",
                start_url=start_url,
                login_indicator_selector=login_indicator_selector,
            )
        else:
            logger.warning(
                "crawl_site_seed_fetch_failed",
                start_url=start_url,
                error=seed_result.error_message,
            )
        return []

    # crawl4ai links shape: {"internal": [{"href": "...", "text": "..."}], "external": [...]}.
    internal = seed_result.links.get("internal") or []
    seeds: list[str] = []
    for entry in internal:
        href = entry.get("href") if isinstance(entry, dict) else None
        if not href:
            continue
        if urlparse(href).netloc.lower() != base_domain:
            continue
        seeds.append(href)
    return seeds


def _build_candidate_set(
    *,
    start_url: str,
    sitemap_urls: list[str],
    bfs_seed_urls: list[str],
    base_domain: str,
    max_pages: int,
    include_patterns: list[str] | None,
) -> list[str]:
    """Union(sitemap, BFS-seeds), filtered to same-domain, deduped, capped.

    AC-2: when union exceeds max_pages, sitemap entries take priority
    over BFS-discovered URLs. ``include_patterns`` (when set) restricts
    candidates by simple substring match on the path — same semantics
    as the old crawl4ai URLPatternFilter without the deserialisation
    fragility (SPEC-CRAWL-001 v0.8.6 issue).

    The starting URL is always included as candidate index 0 so the
    homepage is in the bulk fetch and never silently dropped on cap.
    """

    def _same_domain(u: str) -> bool:
        return urlparse(u).netloc.lower() == base_domain

    def _matches_include(u: str) -> bool:
        if not include_patterns:
            return True
        return any(p in u for p in include_patterns)

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

    # Priority order: start_url first, then sitemap (site-owner truth),
    # then BFS-discovered (best-effort).
    _add(start_url)
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
