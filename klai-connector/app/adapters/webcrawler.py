"""Web crawler connector adapter using the Crawl4AI REST API."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from app.adapters.base import BaseAdapter, DocumentRef, ImageRef
from app.core.config import Settings

logger = structlog.get_logger(__name__)

# Maximum time to poll for a crawl job before marking as PENDING (seconds).
_MAX_POLL_SECONDS = 30 * 60  # 30 minutes

# Interval between poll requests (seconds).
_POLL_INTERVAL = 3

# JS injected BEFORE wait_for: strip nav chrome so the word-count condition fires
# only when article content is present.  Uses only semantic tag/role selectors —
# never class/id substring selectors (see pitfalls/backend.md).
_JS_REMOVE_CHROME = """
[
  'nav', 'header', 'footer', 'aside',
  '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]', '[role="complementary"]',
  '[role="search"]',
].forEach(sel => document.querySelectorAll(sel).forEach(el => el.remove()));
"""

# JS injected AFTER wait_for fires: open collapsed toggles (Notion/details).
_JS_EXPAND_TOGGLES = """
document.querySelectorAll('details:not([open])').forEach(d => d.setAttribute('open', ''));
document.querySelectorAll(
  '.notion-toggle__summary, [data-block-type="toggle"] > *:first-child'
).forEach(s => s.click());
await new Promise(r => setTimeout(r, 300));
"""


@dataclass(frozen=True)
class _CrawlConfig:
    """Typed, validated snapshot of a connector's crawl configuration.

    Constructed once per sync run from the raw connector.config dict so all
    code paths operate on typed fields instead of repeated config.get() calls.
    """

    base_url: str
    max_pages: int
    max_depth: int
    path_prefix: str | None
    content_selector: str | None
    cookies: list[dict[str, Any]] | None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> _CrawlConfig:
        return cls(
            base_url=d["base_url"],
            max_pages=min(d.get("max_pages", 200), 2000),
            max_depth=d.get("max_depth", 3),
            path_prefix=d.get("path_prefix") or None,
            content_selector=d.get("content_selector") or None,
            cookies=d.get("cookies") or None,
        )


def _build_cookie_hooks(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    """Build Crawl4AI hooks payload that injects cookies via on_page_context_created."""
    cookies_json = json.dumps(cookies)
    hook_code = f"""
async def hook(page, context, **kwargs):
    await context.add_cookies({cookies_json})
    return page
"""
    return {"code": {"on_page_context_created": hook_code}, "timeout": 30}


def _extract_markdown(page: dict[str, Any]) -> str:
    """Extract the best available markdown string from a Crawl4AI page result.

    Crawl4AI >= 0.8 returns markdown as a dict with fit_markdown (PruningContentFilter
    output) and raw_markdown (unfiltered). Older versions return a plain string.
    Prefers fit_markdown when available.
    """
    md = page.get("markdown", "")
    if isinstance(md, dict):
        md = md.get("fit_markdown") or md.get("raw_markdown", "")
    md_v2 = page.get("markdown_v2", {})
    return md or md_v2.get("fit_markdown", "") or md_v2.get("raw_markdown", "")


class CrawlJobPendingError(Exception):
    """Raised when a crawl job is still running and needs to be checked later.

    The SyncEngine catches this to set the sync run status to PENDING and
    stores the task_id in cursor_state for the next run.
    """

    def __init__(self, task_id: str, job_started_at: str) -> None:
        self.task_id = task_id
        self.job_started_at = job_started_at
        super().__init__(f"Crawl job {task_id} still pending (started {job_started_at})")


class WebCrawlerAdapter(BaseAdapter):
    """Web crawler adapter that uses Crawl4AI for deep-crawl website ingestion.

    Starts an async crawl job via Crawl4AI's REST API, polls for completion,
    and returns the crawled pages as DocumentRef objects with markdown content.

    list_documents() runs a three-phase pipeline:
      1. BFS discovery  — finds all reachable URLs within path_prefix.
      2. Extraction     — re-fetches with css_selector applied (only when configured).
      3. Sitemap supplement — fills remaining page budget from sitemap.xml.

    Content is held in _crawl_cache until fetch_document() consumes it.
    The caller MUST invoke post_sync() to release per-connector cache memory.
    """

    def __init__(self, settings: Settings) -> None:
        self._api_url = settings.crawl4ai_api_url.rstrip("/")
        self._api_key = settings.crawl4ai_internal_key
        self._http_client = httpx.AsyncClient(http2=True, timeout=30.0)
        # Per-connector cache: {connector_id: {url: markdown}}
        # Populated by list_documents(), consumed by fetch_document(), freed by post_sync().
        self._crawl_cache: dict[str, dict[str, str]] = {}

    async def aclose(self) -> None:
        """Close the persistent HTTP client."""
        await self._http_client.aclose()

    # -- Internal helpers ------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Return authorization headers if an API key is configured."""
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    async def _fetch_sitemap_urls(self, base_url: str) -> list[str]:
        """Fetch same-domain URLs from sitemap.xml. Returns [] on any error."""
        sitemap_url = f"{base_url.rstrip('/')}/sitemap.xml"
        base_domain = urlparse(base_url).netloc.lower()
        try:
            resp = await self._http_client.get(sitemap_url, timeout=10.0)
            resp.raise_for_status()
            locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", resp.text)
            return [u for u in locs if urlparse(u).netloc.lower() == base_domain]
        except Exception:
            return []

    def _build_discovery_params(self) -> dict[str, Any]:
        """Build CrawlerRunConfig params for BFS discovery phase.

        No css_selector, no PruningContentFilter — BFS only needs to follow links.
        word_count_threshold=0 ensures nav-heavy homepages (which may have minimal
        prose) are not skipped during link extraction.
        """
        return {
            "cache_mode": "bypass",
            "word_count_threshold": 0,
            "wait_for": "js:() => document.body.innerText.trim().split(/\\s+/).length > 10",
            "js_code_before_wait": _JS_REMOVE_CHROME,
            "excluded_tags": ["nav", "footer", "header", "aside", "script", "style"],
            "remove_consent_popups": True,
            "remove_overlay_elements": True,
            "page_timeout": 30000,
            "markdown_generator": {
                "type": "DefaultMarkdownGenerator",
                "params": {
                    "options": {"type": "dict", "value": {"ignore_links": False, "body_width": 0}},
                },
            },
        }

    def _build_page_crawl_params(self, cfg: _CrawlConfig) -> dict[str, Any]:
        """Build CrawlerRunConfig params for single-page crawling (no deep_crawl_strategy).

        Used for Phase 2 extraction re-crawl and Phase 3 sitemap supplement.
        Pipeline switching aligned with SPEC-CRAWL-001.
        """
        md_gen_params: dict[str, Any] = {
            "content_filter": {
                "type": "PruningContentFilter",
                "params": {"threshold": 0.45, "threshold_type": "dynamic"},
            },
            "options": {"type": "dict", "value": {"ignore_links": False, "body_width": 0}},
        }
        params: dict[str, Any] = {
            "cache_mode": "bypass",
            "word_count_threshold": 10,
            "wait_for": "js:() => document.body.innerText.trim().split(/\\s+/).length > 50",
            "js_code": _JS_EXPAND_TOGGLES,
            "remove_consent_popups": True,
            "remove_overlay_elements": True,
            "page_timeout": 30000,
            "markdown_generator": {"type": "DefaultMarkdownGenerator", "params": md_gen_params},
        }
        if cfg.content_selector:
            params["css_selector"] = cfg.content_selector
        else:
            params["js_code_before_wait"] = _JS_REMOVE_CHROME
            params["excluded_tags"] = ["nav", "footer", "header", "aside", "script", "style"]
        return params

    async def _start_crawl(
        self,
        cfg: _CrawlConfig,
        crawl_params: dict[str, Any],
        cookies: list[dict[str, Any]] | None = None,
    ) -> str:
        """Submit a BFS crawl job to Crawl4AI and return the task_id.

        Args:
            cfg: Typed connector configuration.
            crawl_params: CrawlerRunConfig params built by _build_discovery_params().
            cookies: Optional list of cookie dicts to inject via on_page_context_created hook.
        """
        deep_crawl_params: dict[str, Any] = {
            "max_depth": cfg.max_depth,
            "max_pages": cfg.max_pages,
        }
        if cfg.path_prefix:
            # Path-only prefix with wildcard — matches all URLs under the prefix
            # regardless of domain. Full-URL patterns without /* are treated as
            # exact matches by URLPatternFilter, filtering out all child pages.
            pattern = "/" + cfg.path_prefix.strip("/") + "/*"
            deep_crawl_params["filter_chain"] = {
                "type": "FilterChain",
                "params": {
                    "filters": [
                        {"type": "URLPatternFilter", "params": {"patterns": [pattern]}},
                    ]
                },
            }

        # Don't mutate the caller's dict; merge into a new one.
        params = {
            **crawl_params,
            "deep_crawl_strategy": {
                "type": "BFSDeepCrawlStrategy",
                "params": deep_crawl_params,
            },
        }

        payload: dict[str, Any] = {
            "urls": [cfg.base_url],
            "crawler_config": {"type": "CrawlerRunConfig", "params": params},
        }
        if cookies:
            payload["hooks"] = _build_cookie_hooks(cookies)

        response = await self._http_client.post(
            f"{self._api_url}/crawl/job",
            json=payload,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        task_id: str = response.json()["task_id"]
        logger.info("crawl_job_started", task_id=task_id, base_url=cfg.base_url)
        return task_id

    async def _poll_task(self, task_id: str) -> dict[str, Any]:
        """Poll the Crawl4AI task endpoint until completion or timeout.

        Returns:
            The full task result payload on completion.

        Raises:
            CrawlJobPendingError: If the job is still running after _MAX_POLL_SECONDS.
        """
        started = datetime.now(UTC)
        elapsed = 0.0

        while elapsed < _MAX_POLL_SECONDS:
            response = await self._http_client.get(
                f"{self._api_url}/crawl/job/{task_id}",
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            data = response.json()
            status = data.get("status", "").lower()

            if status == "completed":
                return data["result"]
            if status == "failed":
                raise RuntimeError(f"Crawl job {task_id} failed: {data.get('error', 'unknown')}")

            await asyncio.sleep(_POLL_INTERVAL)
            elapsed = (datetime.now(UTC) - started).total_seconds()

        raise CrawlJobPendingError(task_id=task_id, job_started_at=started.isoformat())

    def _process_results(
        self,
        data: dict[str, Any],
        cache: dict[str, str],
        base_url: str,
    ) -> list[DocumentRef]:
        """Convert Crawl4AI results into DocumentRef objects and populate the cache.

        Accepts both BFS task result payloads (``{"result": {"results": [...]}}`` after
        unwrapping in _poll_task) and sync /crawl responses (``{"results": [...]}``).
        Skips pages with empty markdown or from a different domain.
        """
        # Normalize: sync /crawl uses "results", BFS task result uses "result" (already
        # unwrapped to the inner dict by _poll_task, so also has "results").
        raw = data.get("results", data.get("result", []))
        if isinstance(raw, dict):
            raw = [raw]
        pages: list[dict[str, Any]] = raw or []

        # Discard results from external domains — Crawl4AI may follow external links.
        base_netloc = urlparse(base_url).netloc.lower()
        pages = [p for p in pages if urlparse(p.get("url", "")).netloc.lower() == base_netloc]

        refs: list[DocumentRef] = []
        skipped: list[str] = []

        for page in pages:
            url: str = page.get("url", "")
            markdown = _extract_markdown(page)

            if not url or not markdown.strip():
                if url:
                    skipped.append(url)
                continue

            parsed = urlparse(url)
            path = parsed.path.strip("/") or "index"
            if not path.endswith((".md", ".html", ".txt")):
                path = f"{path}.md"

            cache[url] = markdown
            content_type = "pdf_document" if url.lower().endswith(".pdf") else "kb_article"

            # Extract images from crawl4ai media field — independent of PruningContentFilter.
            # fit_markdown strips image blocks (score 0.0 against 0.45 threshold), so we
            # cannot rely on ![alt](url) patterns in the markdown text for webcrawler pages.
            raw_images = page.get("media", {}).get("images", [])
            images: list[ImageRef] | None = None
            if raw_images:
                images = [
                    ImageRef(url=img["src"], alt=img.get("alt", ""), source_path="")
                    for img in raw_images
                    if img.get("src")
                ] or None

            refs.append(
                DocumentRef(
                    path=path,
                    ref=url,
                    size=len(markdown.encode("utf-8")),
                    content_type=content_type,
                    source_ref=url,
                    source_url=url,
                    images=images,
                )
            )

        if skipped:
            logger.warning(
                "crawl_pages_skipped",
                count=len(skipped),
                sample=skipped[:5],
            )

        return refs

    async def _batch_crawl_urls(
        self,
        urls: list[str],
        crawl_params: dict[str, Any],
        cache: dict[str, str],
        base_url: str,
        cookies: list[dict[str, Any]] | None = None,
    ) -> list[DocumentRef]:
        """Crawl a list of URLs via the synchronous /crawl endpoint and return DocumentRefs.

        Used for Phase 2 extraction re-crawl (with css_selector applied) and Phase 3
        sitemap supplement. Sends URLs in batches of 100 (Crawl4AI's max_length limit).
        Batch failures are logged as warnings; successfully crawled batches are returned.
        """
        if not urls:
            return []
        refs: list[DocumentRef] = []
        for i in range(0, len(urls), 100):
            batch = urls[i : i + 100]
            payload: dict[str, Any] = {
                "urls": batch,
                "browser_config": {"type": "BrowserConfig", "params": {"text_mode": True}},
                "crawler_config": {"type": "CrawlerRunConfig", "params": crawl_params},
            }
            if cookies:
                payload["hooks"] = _build_cookie_hooks(cookies)
            try:
                response = await self._http_client.post(
                    f"{self._api_url}/crawl",
                    json=payload,
                    headers=self._auth_headers(),
                    timeout=300.0,
                )
                response.raise_for_status()
                refs.extend(self._process_results(response.json(), cache, base_url=base_url))
            except Exception as exc:
                logger.warning("batch_crawl_failed", batch_index=i // 100, error=str(exc))
        return refs

    # -- Three-phase pipeline --------------------------------------------------

    async def _run_discovery(
        self, cfg: _CrawlConfig, cache: dict[str, str],
    ) -> list[DocumentRef]:
        """Phase 1: BFS discovery — finds all linked pages without content filtering."""
        task_id = await self._start_crawl(cfg, self._build_discovery_params(), cfg.cookies)
        result = await self._poll_task(task_id)
        refs = self._process_results(result, cache, base_url=cfg.base_url)
        logger.info("discovery_complete", url_count=len(refs), base_url=cfg.base_url)
        return refs

    async def _run_extraction(
        self,
        cfg: _CrawlConfig,
        refs: list[DocumentRef],
        cache: dict[str, str],
        page_params: dict[str, Any],
    ) -> list[DocumentRef]:
        """Phase 2: extraction re-crawl — re-fetches discovered URLs with css_selector applied.

        Only runs when content_selector is configured. Replaces Phase 1 BFS markdown
        in the cache with selector-scoped content. Skipped when refs is empty.
        """
        if not cfg.content_selector or not refs:
            return refs
        urls = [ref.ref for ref in refs]
        extracted = await self._batch_crawl_urls(urls, page_params, cache, cfg.base_url, cfg.cookies)
        logger.info("extraction_complete", page_count=len(extracted), base_url=cfg.base_url)
        return extracted

    async def _run_sitemap_supplement(
        self,
        cfg: _CrawlConfig,
        refs: list[DocumentRef],
        cache: dict[str, str],
        page_params: dict[str, Any],
    ) -> list[DocumentRef]:
        """Phase 3: sitemap supplement — crawls sitemap.xml URLs not found by BFS.

        Only runs when the page budget (max_pages) is not yet exhausted.
        """
        remaining = cfg.max_pages - len(refs)
        if remaining <= 0:
            return refs
        seen = {ref.ref for ref in refs}
        sitemap_urls = await self._fetch_sitemap_urls(cfg.base_url)
        supplement_urls = [u for u in sitemap_urls if u not in seen][:remaining]
        if not supplement_urls:
            return refs
        logger.info(
            "sitemap_supplement_started",
            supplement_count=len(supplement_urls),
            crawled_so_far=len(refs),
        )
        extra = await self._batch_crawl_urls(
            supplement_urls, page_params, cache, cfg.base_url, cfg.cookies,
        )
        return [*refs, *extra]

    # -- BaseAdapter interface ------------------------------------------------

    async def list_documents(
        self, connector: Any, cursor_context: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> list[DocumentRef]:
        """List crawled pages from the target website using a three-phase pipeline.

        Phases:
          1. BFS discovery — no css_selector; finds all reachable pages.
          2. Extraction re-crawl — re-fetches with css_selector (only when configured).
          3. Sitemap supplement — fills remaining budget from sitemap.xml.

        cursor_context is accepted for interface compatibility. Webcrawler syncs are
        always full re-crawls; incremental sync is not supported.
        """
        connector_id = str(connector.connector_id)
        cache: dict[str, str] = {}
        self._crawl_cache[connector_id] = cache

        cfg = _CrawlConfig.from_dict(connector.config)
        page_params = self._build_page_crawl_params(cfg)

        logger.info(
            "crawl_started",
            base_url=cfg.base_url,
            max_pages=cfg.max_pages,
            authenticated=bool(cfg.cookies),
            has_selector=bool(cfg.content_selector),
        )

        refs = await self._run_discovery(cfg, cache)
        refs = await self._run_extraction(cfg, refs, cache, page_params)
        refs = await self._run_sitemap_supplement(cfg, refs, cache, page_params)

        logger.info("crawl_complete", page_count=len(refs), base_url=cfg.base_url)
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Return the cached markdown content for a crawled page.

        Content is populated during list_documents() and keyed by URL.

        Raises:
            KeyError: If the URL was not found in the crawl cache.
        """
        connector_id = str(connector.connector_id)
        cache = self._crawl_cache.get(connector_id, {})
        if ref.ref not in cache:
            raise KeyError(f"URL not found in crawl cache for connector {connector_id}: {ref.ref}")
        return cache[ref.ref].encode("utf-8")

    async def post_sync(self, connector: Any) -> None:
        """Free the per-connector crawl cache after all documents have been fetched."""
        self._crawl_cache.pop(str(connector.connector_id), None)

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor state for the web crawler."""
        connector_id = str(connector.connector_id)
        return {
            "last_crawl_at": datetime.now(UTC).isoformat(),
            "url_count": len(self._crawl_cache.get(connector_id, {})),
            "base_url": connector.config.get("base_url", ""),
        }
