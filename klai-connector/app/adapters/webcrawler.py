"""Web crawler connector adapter using the Crawl4AI REST API."""

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

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


def _build_cookie_hooks(cookies: list[dict[str, Any]]) -> dict[str, Any]:
    """Build Crawl4AI hooks payload that injects cookies via on_page_context_created."""
    cookies_json = json.dumps(cookies)
    hook_code = f"""
async def hook(page, context, **kwargs):
    await context.add_cookies({cookies_json})
    return page
"""
    return {"code": {"on_page_context_created": hook_code}, "timeout": 30}


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
    """

    def __init__(self, settings: Settings) -> None:
        self._api_url = settings.crawl4ai_api_url.rstrip("/")
        self._api_key = settings.crawl4ai_internal_key
        self._http_client = httpx.AsyncClient(http2=True, timeout=30.0)
        # Cache of crawled content keyed by connector_id: {connector_id: {url: markdown}}.
        # Keyed per connector to avoid cross-contamination during concurrent syncs.
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

    async def _crawl_pages_sync(
        self,
        urls: list[str],
        crawl_params: dict[str, Any],
        cache: dict[str, str],
        base_url: str,
        cookies: list[dict[str, Any]] | None = None,
    ) -> list[DocumentRef]:
        """Crawl a list of URLs via the synchronous /crawl endpoint and return DocumentRefs.

        Used for sitemap supplement: pages that BFS missed because they are not linked.
        Sends URLs in batches of 100 (Crawl4AI's /crawl endpoint max_length limit).
        """
        if not urls:
            return []
        refs: list[DocumentRef] = []
        batch_size = 100
        for i in range(0, len(urls), batch_size):
            batch = urls[i : i + batch_size]
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
                data = response.json()
                refs.extend(self._process_results(data, cache, base_url=base_url))
            except Exception as exc:
                logger.warning("Supplement crawl batch %d failed: %s", i // batch_size, exc)
        return refs

    def _build_discovery_params(self, config: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        """Build CrawlerRunConfig params for BFS discovery phase.

        No css_selector, no PruningContentFilter — BFS only needs links, not filtered prose.
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

    def _build_page_crawl_params(self, config: dict[str, Any]) -> dict[str, Any]:
        """Build CrawlerRunConfig params for single-page crawling (no deep_crawl_strategy).

        Used for sitemap supplement: crawling individual pages that BFS did not find.
        Pipeline switching aligned with SPEC-CRAWL-001.
        """
        content_selector: str | None = config.get("content_selector") or None
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
        if content_selector:
            params["css_selector"] = content_selector
        else:
            params["js_code_before_wait"] = _JS_REMOVE_CHROME
            params["excluded_tags"] = ["nav", "footer", "header", "aside", "script", "style"]
        return params

    async def _start_crawl(
        self,
        config: dict[str, Any],
        crawl_params: dict[str, Any],
        cookies: list[dict[str, Any]] | None = None,
    ) -> str:
        """Submit a BFS crawl job to Crawl4AI and return the task_id.

        Args:
            config: Connector config dict (provides base_url, max_depth, max_pages, path_prefix).
            crawl_params: CrawlerRunConfig params built by the caller (e.g. _build_discovery_params).
            cookies: Optional list of cookie dicts to inject via on_page_context_created hook.
        """
        base_url: str = config["base_url"]
        max_depth: int = config.get("max_depth", 3)
        max_pages: int = min(config.get("max_pages", 200), 2000)
        allowed_path_prefix: str | None = config.get("path_prefix") or None

        deep_crawl_params: dict[str, Any] = {
            "max_depth": max_depth,
            "max_pages": max_pages,
        }
        if allowed_path_prefix:
            pattern = base_url.rstrip("/") + "/" + allowed_path_prefix.lstrip("/")
            deep_crawl_params["filter_chain"] = [
                {"type": "URLPatternFilter", "params": {"patterns": [pattern]}},
            ]

        # Don't mutate the caller's dict — work on a copy.
        params = dict(crawl_params)
        params["deep_crawl_strategy"] = {
            "type": "BFSDeepCrawlStrategy",
            "params": deep_crawl_params,
        }

        payload: dict[str, Any] = {
            "urls": [base_url],
            "crawler_config": {
                "type": "CrawlerRunConfig",
                "params": params,
            },
        }
        if cookies:
            payload["hooks"] = _build_cookie_hooks(cookies)

        response = await self._http_client.post(
            f"{self._api_url}/crawl/job",
            json=payload,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        data = response.json()
        task_id: str = data["task_id"]
        logger.info("Started crawl job", task_id=task_id, base_url=base_url)
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
                error_msg = data.get("error", "Unknown crawl error")
                raise RuntimeError(f"Crawl job {task_id} failed: {error_msg}")

            await asyncio.sleep(_POLL_INTERVAL)
            elapsed = (datetime.now(UTC) - started).total_seconds()

        # Timeout: signal to the SyncEngine to mark as PENDING.
        raise CrawlJobPendingError(
            task_id=task_id,
            job_started_at=started.isoformat(),
        )

    def _process_results(
        self, data: dict[str, Any], cache: dict[str, str], base_url: str,
    ) -> list[DocumentRef]:
        """Convert crawl results into DocumentRef objects and populate the cache.

        Args:
            data: Raw Crawl4AI task result payload.
            cache: Per-connector cache dict to populate (url -> markdown).
            base_url: Origin URL — results from other domains are discarded.

        Skips pages with empty or missing markdown content.
        """
        refs: list[DocumentRef] = []
        results = data.get("results", data.get("result", []))
        if isinstance(results, dict):
            results = [results]

        # Always restrict to origin domain — Crawl4AI may follow external links.
        base_netloc = urlparse(base_url).netloc.lower()
        results = [p for p in results if urlparse(p.get("url", "")).netloc.lower() == base_netloc]

        warnings: list[str] = []

        for page in results:
            url: str = page.get("url", "")
            # crawl4ai >= 0.8 returns `markdown` as a dict; prefer fit_markdown
            # (output of PruningContentFilter) over raw_markdown.
            _md = page.get("markdown", "")
            if isinstance(_md, dict):
                _md = _md.get("fit_markdown") or _md.get("raw_markdown", "")
            _md_v2 = page.get("markdown_v2", {})
            markdown: str = (
                _md
                or _md_v2.get("fit_markdown", "")
                or _md_v2.get("raw_markdown", "")
            )

            if not url or not markdown or not markdown.strip():
                if url:
                    warnings.append(url)
                continue

            # Derive a path from the URL for display purposes.
            parsed = urlparse(url)
            path = parsed.path.strip("/") or "index"
            if not path.endswith((".md", ".html", ".txt")):
                path = f"{path}.md"

            content_bytes = markdown.encode("utf-8")
            cache[url] = markdown

            ingest_content_type = (
                "pdf_document" if url.lower().endswith(".pdf") else "kb_article"
            )

            refs.append(
                DocumentRef(
                    path=path,
                    ref=url,
                    size=len(content_bytes),
                    content_type=ingest_content_type,
                    source_ref=url,
                    source_url=url,
                )
            )

        if warnings:
            logger.warning(
                "Skipped %d pages with empty content: %s",
                len(warnings),
                ", ".join(warnings[:5]),
            )

        return refs

    # -- BaseAdapter interface ------------------------------------------------

    async def list_documents(
        self, connector: Any, cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List crawled pages from the target website using a two-phase approach.

        Phase 1 — BFS discovery via /crawl/job:
            Submits a BFS job with no content_selector so the crawler follows all
            links from base_url regardless of page structure. Collects all reachable
            URLs within path_prefix (when set).

        Phase 2 — Extraction re-crawl (only when content_selector is set):
            Re-crawls the discovered URLs via /crawl (sync) with css_selector applied
            so only the matching DOM element is extracted as markdown. Skipped when no
            content_selector is configured; Phase 1 BFS markdown is used directly.

        Phase 3 — Sitemap supplement:
            Fetches sitemap.xml and crawls any URLs not yet in the cache, up to
            max_pages. Runs after both phases regardless of content_selector.
        """
        connector_id = str(connector.connector_id)
        cache: dict[str, str] = {}
        self._crawl_cache[connector_id] = cache
        config: dict[str, Any] = connector.config
        base_url: str = config.get("base_url", "")
        max_pages: int = min(config.get("max_pages", 200), 2000)
        content_selector: str | None = config.get("content_selector") or None
        cookies: list[dict[str, Any]] | None = config.get("cookies") or None
        page_params = self._build_page_crawl_params(config)

        logger.info(
            "Starting crawl",
            base_url=base_url,
            max_pages=max_pages,
            authenticated=bool(cookies),
            has_selector=bool(content_selector),
        )

        # Phase 1: BFS discovery (no selector — finds all linked pages).
        discovery_params = self._build_discovery_params(config)
        task_id = await self._start_crawl(config, discovery_params, cookies)
        result = await self._poll_task(task_id)
        refs = self._process_results(result, cache, base_url=base_url)
        logger.info("BFS discovery complete", urls_found=len(refs))

        # Phase 2: extraction re-crawl (only when content_selector is configured).
        if content_selector and refs:
            urls = [ref.ref for ref in refs]
            refs = await self._crawl_pages_sync(urls, page_params, cache, base_url=base_url, cookies=cookies)
            logger.info("Extraction complete", pages_with_content=len(refs))

        # Phase 3: sitemap supplement — fill remaining slots from sitemap.
        if len(refs) < max_pages:
            remaining = max_pages - len(refs)
            seen_urls = {ref.ref for ref in refs}
            sitemap_urls = await self._fetch_sitemap_urls(base_url)
            supplement_urls = [u for u in sitemap_urls if u not in seen_urls][:remaining]
            if supplement_urls:
                logger.info(
                    "Supplementing with sitemap URLs",
                    supplement_count=len(supplement_urls),
                    crawled_so_far=len(refs),
                )
                supplement_refs = await self._crawl_pages_sync(
                    supplement_urls, page_params, cache, base_url=base_url, cookies=cookies,
                )
                refs.extend(supplement_refs)

        logger.info("Crawl complete", total_pages=len(refs), base_url=base_url)
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Return the cached markdown content for a crawled page.

        Content is populated during list_documents() and keyed by URL,
        stored under the connector's ID to avoid cross-sync contamination.

        Raises:
            KeyError: If the URL was not found in the crawl cache.
        """
        connector_id = str(connector.connector_id)
        cache = self._crawl_cache.get(connector_id, {})
        url = ref.ref
        if url not in cache:
            raise KeyError(f"URL not found in crawl cache for connector {connector_id}: {url}")
        return cache[url].encode("utf-8")

    async def post_sync(self, connector: Any) -> None:
        """Free the per-connector crawl cache after all documents have been fetched."""
        self._crawl_cache.pop(str(connector.connector_id), None)

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor state for the web crawler.

        Contains the base URL and page count for comparison on next sync.
        """
        config: dict[str, Any] = connector.config
        connector_id = str(connector.connector_id)
        url_count = len(self._crawl_cache.get(connector_id, {}))
        return {
            "last_crawl_at": datetime.now(UTC).isoformat(),
            "url_count": url_count,
            "base_url": config.get("base_url", ""),
        }
