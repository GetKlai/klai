"""Async httpx client for the knowledge-ingest /ingest/v1/document endpoint."""

from urllib.parse import urlparse

import httpx
from klai_image_storage import dedupe_image_urls

from app.core.logging import get_logger

logger = get_logger(__name__)


# @MX:NOTE: Web crawls must declare source_type="crawl" + source_domain so
#   knowledge-ingest's compute_source_label() labels chunks with the actual
#   domain (e.g. "help.voys.nl") instead of the connector_type slug.
# @MX:REASON: source_label drives the Facet API and retrieval source routing
#   (SPEC-KB-021). Labelling every web_crawler chunk with "web_crawler" makes
#   per-domain filtering impossible.
_CRAWL_CONNECTOR_TYPES = {"web_crawler"}


def _build_payload(
    *,
    org_id: str,
    kb_slug: str,
    path: str,
    content: str,
    source_connector_id: str,
    source_ref: str,
    source_url: str = "",
    content_type: str = "unknown",
    image_urls: list[str] | None = None,
    connector_type: str = "",
    sender_email: str = "",
    mentioned_emails: list[str] | None = None,
) -> dict:
    """Build the JSON payload for the knowledge-ingest endpoint."""
    is_crawl = connector_type in _CRAWL_CONNECTOR_TYPES
    source_type = "crawl" if is_crawl else "connector"

    payload: dict = {
        "org_id": org_id,
        "kb_slug": kb_slug,
        "path": path,
        "content": content,
        "source_connector_id": source_connector_id,
        "source_ref": source_ref,
        "content_type": content_type,
        "source_type": source_type,
    }
    if connector_type:
        payload["connector_type"] = connector_type
    if is_crawl and source_url:
        host = urlparse(source_url).hostname
        if host:
            payload["source_domain"] = host
    extra: dict[str, object] = {}
    if source_url:
        extra["source_url"] = source_url
    if image_urls:
        extra["image_urls"] = dedupe_image_urls(image_urls)
    if sender_email:
        extra["sender_email"] = sender_email
    if mentioned_emails:
        extra["mentioned_emails"] = mentioned_emails
    if extra:
        payload["extra"] = extra
    return payload


class KnowledgeIngestClient:
    """HTTP client for the knowledge-ingest service.

    Sends parsed document content to the existing ``/ingest/v1/document``
    endpoint for embedding and storage in Qdrant.

    Args:
        base_url: Base URL of the knowledge-ingest service (e.g. ``http://knowledge-ingest:8100``).
        internal_secret: Value for the ``X-Internal-Secret`` header (service-to-service auth).
    """

    def __init__(self, base_url: str, internal_secret: str = "") -> None:
        self._internal_secret = internal_secret
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60.0)

    async def ingest_document(
        self,
        org_id: str,
        kb_slug: str,
        path: str,
        content: str,
        source_connector_id: str,
        source_ref: str,
        source_url: str = "",
        content_type: str = "unknown",
        allowed_assertion_modes: list[str] | None = None,
        image_urls: list[str] | None = None,
        connector_type: str = "",
    ) -> None:
        """Send a parsed document to knowledge-ingest for embedding.

        Args:
            org_id: Organisation UUID string.
            kb_slug: Knowledge base slug (default ``"org"``).
            path: Document path within the source.
            content: Extracted text content.
            source_connector_id: Connector UUID string for deduplication.
            source_ref: Source reference string (e.g. ``owner/repo:branch:path``).
            content_type: Semantic content type (e.g. ``kb_article``, ``pdf_document``).
            allowed_assertion_modes: Optional connector-level hint for which assertion modes
                this source can produce. Used in knowledge-ingest when content has no frontmatter.
            image_urls: Optional list of presigned S3 URLs for images extracted from the document.

        Raises:
            httpx.HTTPStatusError: If the ingest endpoint returns an error status.
        """
        headers: dict[str, str] = {}
        if self._internal_secret:
            headers["x-internal-secret"] = self._internal_secret

        payload = _build_payload(
            org_id=org_id,
            kb_slug=kb_slug,
            path=path,
            content=content,
            source_connector_id=source_connector_id,
            source_ref=source_ref,
            source_url=source_url,
            content_type=content_type,
            image_urls=image_urls,
            connector_type=connector_type,
        )
        if allowed_assertion_modes is not None:
            payload["allowed_assertion_modes"] = allowed_assertion_modes

        response = await self._client.post(
            "/ingest/v1/document",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        logger.info("Ingested document: %s", path)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()


# @MX:ANCHOR: CrawlSyncClient -- delegation boundary for SPEC-CRAWLER-004 Fase D.
# @MX:REASON: Any change here changes the on-wire contract between klai-connector
#   and the knowledge-ingest /ingest/v1/crawl/sync endpoint. Field names map 1:1
#   to CrawlSyncRequest in knowledge_ingest/routes/crawl_sync.py — keep them
#   synchronised.
class CrawlSyncClient:
    """HTTP client for the knowledge-ingest bulk-sync endpoint.

    SPEC-CRAWLER-004 Fase D replaces ``WebCrawlerAdapter.list_documents`` +
    ``fetch_document`` with a single POST to
    ``/ingest/v1/crawl/sync``. klai-connector never sees the decrypted
    cookies — it only sends the ``connector_id`` and knowledge-ingest loads
    the cookies itself via the shared credentials lib (REQ-01.3).

    The returned ``job_id`` is stored on ``sync_run.cursor_state`` and
    polled via :meth:`crawl_sync_status` until the remote job finishes.
    """

    def __init__(self, base_url: str, internal_secret: str = "", timeout: float = 30.0) -> None:
        self._internal_secret = internal_secret
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    def _headers(self) -> dict[str, str]:
        return {"x-internal-secret": self._internal_secret} if self._internal_secret else {}

    async def crawl_sync(
        self,
        *,
        connector_id: str,
        org_id: str,
        kb_slug: str,
        config: dict,
    ) -> dict:
        """Enqueue a bulk crawl via ``POST /ingest/v1/crawl/sync``.

        Returns:
            The raw JSON body — ``{"job_id": str, "status": "queued"}``.

        Raises:
            httpx.HTTPStatusError: on 4xx/5xx. Callers mark sync_runs as
                failed when this happens (SPEC REQ-03.5).
        """
        body = {
            "connector_id": connector_id,
            "org_id": org_id,
            "kb_slug": kb_slug,
            "base_url": config["base_url"],
            "max_pages": int(config.get("max_pages", 200)),
            "max_depth": int(config.get("max_depth", 3)),
            "path_prefix": config.get("path_prefix"),
            "content_selector": config.get("content_selector"),
            "canary_url": config.get("canary_url"),
            "canary_fingerprint": config.get("canary_fingerprint"),
            "login_indicator": config.get("login_indicator_selector"),
        }
        resp = await self._client.post(
            "/ingest/v1/crawl/sync",
            json=body,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def crawl_sync_status(self, job_id: str) -> dict:
        """Poll ``GET /ingest/v1/crawl/sync/{job_id}/status``.

        Returns:
            ``{"job_id", "status", "pages_total", "pages_done", "error"}``.

        Raises:
            httpx.HTTPStatusError: 404 when the job row has been deleted.
        """
        resp = await self._client.get(
            f"/ingest/v1/crawl/sync/{job_id}/status",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
