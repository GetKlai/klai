"""Base adapter ABC defining the interface for all source connectors."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class DocumentRef:
    """Reference to a document in an external source.

    Attributes:
        path: Document path within the source (e.g., "docs/guide.md").
        ref: Source-specific reference (e.g., git blob SHA).
        size: File size in bytes.
        content_type: MIME type or file extension.
    """

    path: str
    ref: str
    size: int
    content_type: str


class BaseAdapter(ABC):
    """Abstract base class for source connectors.

    Each connector type (GitHub, Notion, SharePoint, etc.)
    implements this interface.
    """

    @abstractmethod
    async def list_documents(self, connector: Any) -> list[DocumentRef]:
        """List all documents available for sync from the external source."""
        ...

    @abstractmethod
    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Download the content of a single document."""
        ...

    @abstractmethod
    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return the current cursor state for incremental sync."""
        ...
