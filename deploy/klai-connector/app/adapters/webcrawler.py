"""Web crawler connector adapter using the Crawl4AI REST API."""

import asyncio
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


class CrawlJobPending(Exception):
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

    async def _start_crawl(self, config: dict[str, Any]) -> str:
        """Submit a crawl job to Crawl4AI and return the task_id."""
        base_url: str = config["base_url"]
        max_depth: int = config.get("max_depth", 3)
        max_pages: int = min(config.get("max_pages", 200), 2000)
        allowed_path_prefix: str | None = config.get("path_prefix") or None

        crawler_config: dict[str, Any] = {
            "deep_crawl_strategy": {
                "type": "BFS",
                "max_depth": max_depth,
                "max_pages": max_pages,
            },
            "scraping_strategy": {"type": "LXMLWebScrapingStrategy"},
            "markdown_generator": {
                "type": "DefaultMarkdownGenerator",
                "options": {"ignore_links": False, "body_width": 0},
            },
        }

        if allowed_path_prefix:
            crawler_config["deep_crawl_strategy"]["filter_chain"] = [
                {"type": "URLPatternFilter", "patterns": [allowed_path_prefix]},
            ]

        payload = {
            "urls": [base_url],
            "crawler_config": crawler_config,
        }

        response = await self._http_client.post(
            f"{self._api_url}/crawl",
            json=payload,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        data = response.json()
        task_id: str = data["task_id"]
        logger.info("Started crawl job %s for %s", task_id, base_url)
        return task_id

    async def _poll_task(self, task_id: str) -> dict[str, Any]:
        """Poll the Crawl4AI task endpoint until completion or timeout.

        Returns:
            The full task result payload on completion.

        Raises:
            CrawlJobPending: If the job is still running after _MAX_POLL_SECONDS.
        """
        started = datetime.now(UTC)
        elapsed = 0.0

        while elapsed < _MAX_POLL_SECONDS:
            response = await self._http_client.get(
                f"{self._api_url}/task/{task_id}",
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            data = response.json()
            status = data.get("status", "").lower()

            if status == "completed":
                return data
            if status == "failed":
                error_msg = data.get("error", "Unknown crawl error")
                raise RuntimeError(f"Crawl job {task_id} failed: {error_msg}")

            await asyncio.sleep(_POLL_INTERVAL)
            elapsed = (datetime.now(UTC) - started).total_seconds()

        # Timeout: signal to the SyncEngine to mark as PENDING.
        raise CrawlJobPending(
            task_id=task_id,
            job_started_at=started.isoformat(),
        )

    def _process_results(
        self, data: dict[str, Any], cache: dict[str, str]
    ) -> list[DocumentRef]:
        """Convert crawl results into DocumentRef objects and populate the cache.

        Args:
            data: Raw Crawl4AI task result payload.
            cache: Per-connector cache dict to populate (url -> markdown).

        Skips pages with empty or missing markdown content.
        """
        refs: list[DocumentRef] = []
        results = data.get("results", data.get("result", []))
        if isinstance(results, dict):
            results = [results]

        warnings: list[str] = []

        for page in results:
            url: str = page.get("url", "")
            markdown: str = page.get("markdown", "") or page.get("markdown_v2", {}).get("raw_markdown", "")

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

            refs.append(
                DocumentRef(
                    path=path,
                    ref=url,
                    size=len(content_bytes),
                    content_type=".md",
                    source_ref=url,
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
        """List crawled pages from the target website.

        If a previous run left a pending crawl job (stored in cursor_context),
        checks its status first. Otherwise starts a new crawl.

        Uses a per-connector cache keyed by connector.id to avoid
        cross-contamination during concurrent syncs.

        Raises:
            CrawlJobPending: If the crawl is still running (SyncEngine handles this).
        """
        connector_id = str(connector.id)
        cache: dict[str, str] = {}
        self._crawl_cache[connector_id] = cache
        config: dict[str, Any] = connector.config

        # Resume a pending job from a previous sync run.
        pending_task_id = (cursor_context or {}).get("pending_task_id")
        if pending_task_id:
            logger.info("Resuming pending crawl job %s", pending_task_id)
            data = await self._poll_task(pending_task_id)
            refs = self._process_results(data, cache)
            logger.info("Crawl job %s completed: %d pages", pending_task_id, len(refs))
            return refs

        # Start a new crawl job.
        task_id = await self._start_crawl(config)
        data = await self._poll_task(task_id)
        refs = self._process_results(data, cache)
        logger.info("Crawl completed: %d pages from %s", len(refs), config.get("base_url"))
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Return the cached markdown content for a crawled page.

        Content is populated during list_documents() and keyed by URL,
        stored under the connector's ID to avoid cross-sync contamination.

        Raises:
            KeyError: If the URL was not found in the crawl cache.
        """
        connector_id = str(connector.id)
        cache = self._crawl_cache.get(connector_id, {})
        url = ref.ref
        if url not in cache:
            raise KeyError(f"URL not found in crawl cache for connector {connector_id}: {url}")
        return cache[url].encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor state for the web crawler.

        Contains the base URL and page count for comparison on next sync.
        """
        config: dict[str, Any] = connector.config
        connector_id = str(connector.id)
        url_count = len(self._crawl_cache.get(connector_id, {}))
        return {
            "last_crawl_at": datetime.now(UTC).isoformat(),
            "url_count": url_count,
            "base_url": config.get("base_url", ""),
        }
