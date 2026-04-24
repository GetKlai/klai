"""Shared content-addressed image storage for Klai services.

SPEC-KB-IMAGE-002 — consolidates the duplicated image pipeline that used
to live in both ``klai-connector/app/services/`` and
``klai-knowledge-ingest/knowledge_ingest/`` into a single path-installed
package. Both services import from here; neither keeps a local copy.

Public API (all re-exported at package root):

- :class:`ImageStore` — async S3 client with content-addressed keys and
  dedup
- :class:`ImageUploadResult` — result dataclass returned by
  :meth:`ImageStore.upload_image`
- :class:`ParsedImage` — value object for pre-decoded images supplied
  by a document parser (see :func:`download_and_upload_adapter_images`)
- :func:`download_and_upload_adapter_images` — connector sync engine
  orchestrator (markdown URLs + optional :class:`ParsedImage` list)
- :func:`download_and_upload_crawl_images` — web-crawl orchestrator
  (crawl4ai ``media.images`` dicts)
- :func:`extract_markdown_image_urls`, :func:`is_valid_image_src`,
  :func:`resolve_relative_url`, :func:`dedupe_image_urls` — URL helpers

Size + URL-path invariants are intentionally module-private. They are
wire-level contracts — every uploaded image's URL depends on them —
and consumers should never override them.
"""

from klai_image_storage.pipeline import (
    download_and_upload_adapter_images,
    download_and_upload_crawl_images,
)
from klai_image_storage.storage import (
    ImageStore,
    ImageUploadResult,
)
from klai_image_storage.types import ParsedImage
from klai_image_storage.url_guard import (
    PinnedResolverTransport,
    SsrfBlockedError,
    ValidatedURL,
    validate_image_url,
    validate_url_pinned,
    validate_url_pinned_sync,
)
from klai_image_storage.utils import (
    dedupe_image_urls,
    extract_markdown_image_urls,
    is_valid_image_src,
    resolve_relative_url,
)

__all__ = [
    "ImageStore",
    "ImageUploadResult",
    "ParsedImage",
    "PinnedResolverTransport",
    "SsrfBlockedError",
    "ValidatedURL",
    "dedupe_image_urls",
    "download_and_upload_adapter_images",
    "download_and_upload_crawl_images",
    "extract_markdown_image_urls",
    "is_valid_image_src",
    "resolve_relative_url",
    "validate_image_url",
    "validate_url_pinned",
    "validate_url_pinned_sync",
]
