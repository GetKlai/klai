"""Base adapter ABC defining the interface for all source connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ImageRef:
    """Reference to an image discovered in a document.

    Attributes:
        url: Absolute HTTP(S) URL to the image. Adapters MUST resolve any
            relative URLs to absolute before constructing an ImageRef so that
            the sync engine can download without connector-specific context.
        alt: Alt text from the image reference (empty string if absent).
        source_path: Path or block-id of the image relative to the source
            (empty string when not meaningful, e.g. for crawled web pages).
    """

    url: str
    source_path: str
    alt: str = ""


@dataclass
class DocumentRef:
    """Reference to a document in an external source.

    Attributes:
        path: Document path within the source (e.g., "docs/guide.md").
        ref: Source-specific reference (e.g., git blob SHA).
        size: File size in bytes.
        content_type: MIME type or file extension.
        source_ref: Adapter-specific source reference string
            (e.g., "owner/repo:branch:path" for GitHub, full URL for web crawler).
        source_url: User-visible, clickable URL for this document used in
            citations (e.g. GitHub blob view, Notion page URL, Drive webViewLink).
        last_edited: ISO 8601 timestamp of the last edit in the source,
            used by the sync engine for reconciliation.
        images: URL-based images associated with this document, populated by
            the adapter during list_documents() or fetch_document(). Each
            ImageRef.url MUST be absolute. Embedded images from binary formats
            (DOCX/PDF) are handled separately via the parser pipeline, not here.
        sender_email: Raw email string captured from the source's ``created_by``
            or ``author`` field (e.g., Confluence ``created_by.email``,
            Airtable creator field).  Stored as-is — no normalisation, no
            plus-tag stripping, no role-mailbox denylist.  Entity resolution
            (persons/orgs tables, cross-source merging) is explicitly out of
            scope; see ADR-KB-ENTITIES-DEFERRED.  Empty string when unavailable.
        mentioned_emails: Raw email strings captured from ``mentioned`` /
            ``collaborators`` fields in the source document.  Same
            no-normalisation contract as ``sender_email``.  Empty list when
            unavailable.  Uses ``field(default_factory=list)`` so instances
            never share a mutable default.
    """

    path: str
    ref: str
    size: int
    content_type: str
    source_ref: str = ""
    source_url: str = ""
    last_edited: str = ""  # ISO 8601 from source (used by sync engine for reconciliation)
    images: list[ImageRef] | None = None
    sender_email: str = ""
    mentioned_emails: list[str] = field(default_factory=lambda: [])
    # @MX:NOTE: content_fingerprint field (SPEC-CRAWL-003 REQ-12) removed in
    # SPEC-CRAWLER-004 Fase F — only WebCrawlerAdapter populated it, and that
    # adapter has been deleted now that bulk crawls go through the delegation
    # path in sync_engine._run_web_crawler_delegation.


class BaseAdapter(ABC):
    """Abstract base class for source connectors.

    Each connector type (GitHub, Notion, SharePoint, etc.)
    implements this interface.
    """

    @abstractmethod
    async def list_documents(
        self, connector: Any, cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List all documents available for sync from the external source.

        Args:
            connector: Connector model instance.
            cursor_context: Previous sync run's cursor_state, if any.
        """
        ...

    @abstractmethod
    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Download the content of a single document."""
        ...

    @abstractmethod
    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return the current cursor state for incremental sync."""
        ...

    async def post_sync(self, connector: Any) -> None:
        """Called after all documents have been fetched for a sync run.

        Override to release per-sync resources (e.g. in-memory caches).
        Default implementation is a no-op.
        """
        return None
