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
        extra: Adapter-specific structured metadata forwarded to the ingest
            payload. Empty by default so existing adapters remain unchanged.
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
    extra: dict[str, Any] = field(default_factory=dict)
    # @MX:NOTE: content_fingerprint field (SPEC-CRAWL-003 REQ-12) removed in
    # SPEC-CRAWLER-004 Fase F — only WebCrawlerAdapter populated it, and that
    # adapter has been deleted now that bulk crawls go through the delegation
    # path in sync_engine._run_web_crawler_delegation.


class BaseAdapter(ABC):
    """Abstract base class for source connectors.

    Each connector type (GitHub, Notion, SharePoint, etc.)
    implements this interface.
    """

    def listing_is_complete(self, connector: Any) -> bool:
        """Whether the last ``list_documents`` call for *connector* returned every
        document the source holds under the connector's configuration.

        The sync engine deletes stored refs that are missing from the listing
        only when this is True. A listing that was incremental (a change feed),
        capped by a per-run limit, or truncated by the source must answer False,
        because every document it never saw would otherwise look deleted. The
        default is False so destructive reconciliation stays opt-in.
        """
        return vars(self).get("_listings", {}).get(str(connector.id)) == ""

    def listing_incomplete_reason(self, connector: Any) -> str:
        """Why the last listing for *connector* was not complete ("" when it was)."""
        return vars(self).get("_listings", {}).get(str(connector.id), "not_recorded")

    def forget_listing(self, connector: Any) -> None:
        """Clear the completeness flag; the sync engine calls this before every listing.

        Without it, a code path that returns without recording (an early return,
        a mode added later, a connector switched to an incremental mode) would
        inherit True from an earlier run and delete everything it did not list.
        """
        vars(self).get("_listings", {}).pop(str(connector.id), None)

    def _record_listing(self, connector: Any, *, complete: bool, reason: str = "") -> None:
        """Remember whether the listing just produced for *connector* was complete,
        and if not, why (the cap or mode), so a held-back cleanup can say so."""
        # Kept per instance (adapters are process singletons shared by all
        # connectors), so subclasses need no __init__ cooperation.
        listings: dict[str, str] = vars(self).setdefault("_listings", {})
        listings[str(connector.id)] = "" if complete else (reason or "incomplete")

    async def confirm_gone(self, connector: Any, source_refs: list[str]) -> set[str]:
        """Return the subset of *source_refs*, missing from a complete listing, that is gone.

        By default a ref missing from a complete listing is gone. An adapter whose
        listing is complete but not a reliable proof of absence (a search index
        that lags) overrides this to check each candidate at the source.
        """
        return set(source_refs)

    @abstractmethod
    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
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

    def get_sync_metrics(self, connector: Any) -> dict[str, int]:
        """Return adapter-specific counters for the current sync lifecycle."""
        return {}

    async def post_sync(self, connector: Any) -> None:
        """Called after all documents have been fetched for a sync run.

        Override to release per-sync resources (e.g. in-memory caches).
        Default implementation is a no-op.
        """
        return None
