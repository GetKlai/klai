"""Async httpx client for the knowledge-ingest /ingest/v1/document endpoint."""

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


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
    ) -> None:
        """Send a parsed document to knowledge-ingest for embedding.

        Args:
            org_id: Organisation UUID string.
            kb_slug: Knowledge base slug (default ``"org"``).
            path: Document path within the source.
            content: Extracted text content.
            source_connector_id: Connector UUID string for deduplication.
            source_ref: Source reference string (e.g. ``owner/repo:branch:path``).

        Raises:
            httpx.HTTPStatusError: If the ingest endpoint returns an error status.
        """
        headers: dict[str, str] = {}
        if self._internal_secret:
            headers["x-internal-secret"] = self._internal_secret

        response = await self._client.post(
            "/ingest/v1/document",
            json={
                "org_id": org_id,
                "kb_slug": kb_slug,
                "path": path,
                "content": content,
                "source_connector_id": source_connector_id,
                "source_ref": source_ref,
            },
            headers=headers,
        )
        response.raise_for_status()
        logger.info("Ingested document: %s", path)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
