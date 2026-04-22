"""HTTP client for the Crawl4AI REST API (shared Docker container).

Replaces direct crawl4ai Python library usage.  All crawl requests go through
the REST API at ``settings.crawl4ai_api_url`` so knowledge-ingest does not need
the crawl4ai package (or a local Chromium install) as a dependency.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

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
        params["css_selector"] = selector
        params["excluded_tags"] = []
    else:
        params["js_code_before_wait"] = JS_REMOVE_CHROME
        params["excluded_tags"] = ["nav", "footer", "header", "aside", "script", "style"]

    return params


# ---------------------------------------------------------------------------
# REST API helpers
# ---------------------------------------------------------------------------

_DEEP_POLL_INTERVAL = 5.0  # seconds between polls (bulk/deep crawl)
_MAX_DEEP_POLL = 30 * 60   # max seconds for bulk/deep crawl (30 minutes)


def _auth_headers() -> dict[str, str]:
    if settings.crawl4ai_api_key:
        return {"Authorization": f"Bearer {settings.crawl4ai_api_key}"}
    return {}


async def _fetch_sitemap_urls(base_url: str) -> list[str]:
    """Fetch same-domain URLs from sitemap.xml.

    Best-effort — returns [] on any error (sitemap is optional).
    """
    sitemap_url = base_url.rstrip("/") + "/sitemap.xml"
    base_domain = urlparse(base_url).netloc.lower()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(sitemap_url, headers=_auth_headers())
            resp.raise_for_status()
            locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", resp.text)
            return [u for u in locs if urlparse(u).netloc.lower() == base_domain]
    except Exception:
        return []


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
    max_depth: int = 2,
    max_pages: int = 200,
    include_patterns: list[str] | None = None,
    login_indicator_selector: str | None = None,
    cookies: list[dict[str, Any]] | None = None,
) -> list[CrawlResult]:
    """Deep-crawl a website using BFS strategy via the Crawl4AI REST API.

    Crawls from start_url using BFS up to max_depth and max_pages.

    Aligned with klai-connector's webcrawler (SPEC-CRAWL-001).
    Uses POST /crawl/job → GET /crawl/job/{task_id} (bulk/deep-crawl endpoint).

    Note: exclude_patterns is not supported by Crawl4AI's filter_chain; pass
    include_patterns to restrict crawling to specific URL path prefixes.

    ``login_indicator_selector`` (SPEC-CRAWLER-004 Fase B / REQ-02.3) is
    injected into the wait_for expression — when it matches on a page,
    crawl4ai times out and returns ``success=False`` for that result.
    Callers of ``crawl_site`` can then treat any success=False outcome
    that fired with a non-None selector as an auth-wall event.
    """
    config = build_crawl_config(selector, login_indicator_selector=login_indicator_selector)

    # Derive origin domain for post-crawl filtering.
    parsed = urlparse(start_url)

    deep_crawl_params: dict[str, Any] = {
        "max_depth": max_depth,
        "max_pages": max_pages,
    }
    if include_patterns:
        deep_crawl_params["filter_chain"] = [
            {"type": "URLPatternFilter", "params": {"patterns": include_patterns}},
        ]

    config["deep_crawl_strategy"] = {
        "type": "BFSDeepCrawlStrategy",
        "params": deep_crawl_params,
    }

    payload: dict[str, Any] = {
        "urls": [start_url],
        "crawler_config": {"type": "CrawlerRunConfig", "params": config},
    }
    if cookies:
        # SPEC-CRAWLER-004 Fase C — inject browser cookies via the same
        # on_page_context_created hook crawl_page() uses. Shared _build_cookie_hooks
        # keeps the injection identical across single-page and BFS crawls.
        payload["hooks"] = _build_cookie_hooks(cookies)

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{settings.crawl4ai_api_url}/crawl/job",
            json=payload,
            headers=_auth_headers(),
        )
        resp.raise_for_status()
        task_id: str = resp.json()["task_id"]
        logger.info("crawl_site_job_submitted", start_url=start_url, task_id=task_id)

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
            status = data.get("status", "").lower()
            if status == "completed":
                result_data = data["result"]
                break
            if status == "failed":
                raise RuntimeError(
                    f"Crawl job {task_id} failed: {data.get('error', 'unknown')}"
                )
        else:
            raise TimeoutError(f"Crawl job {task_id} did not complete within {_MAX_DEEP_POLL}s")

    results = result_data.get("results", result_data.get("result", []))
    if isinstance(results, dict):
        results = [results]

    all_results = [_extract_result(start_url, page) for page in results if page]

    # Always restrict to origin domain — Crawl4AI may follow external links during BFS.
    crawl_results = [r for r in all_results if urlparse(r.url).netloc == parsed.netloc]
    skipped = len(all_results) - len(crawl_results)
    logger.info(
        "crawl_site_bfs_complete",
        start_url=start_url,
        pages=len(crawl_results),
        skipped_external=skipped,
    )

    # Phase 2: supplement with sitemap URLs if BFS returned fewer pages than requested.
    # BFS only finds pages reachable via links; orphaned pages (in sitemap but not linked)
    # would be missed without this step.
    if len(crawl_results) < max_pages:
        remaining = max_pages - len(crawl_results)
        seen_urls = {r.url for r in crawl_results}
        sitemap_urls = await _fetch_sitemap_urls(start_url)
        supplement_urls = [u for u in sitemap_urls if u not in seen_urls][:remaining]
        if supplement_urls:
            logger.info(
                "crawl_site_supplement",
                start_url=start_url,
                bfs_pages=len(crawl_results),
                supplement_count=len(supplement_urls),
            )
            supplement_results = await asyncio.gather(
                *[crawl_page(u, selector=selector) for u in supplement_urls],
                return_exceptions=True,
            )
            for result in supplement_results:
                if isinstance(result, CrawlResult) and result.success and (
                    result.fit_markdown or result.raw_markdown
                ):
                    crawl_results.append(result)

    logger.info("crawl_site_complete", start_url=start_url, pages=len(crawl_results))
    return crawl_results


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
