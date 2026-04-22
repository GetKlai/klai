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
- :func:`download_and_upload_adapter_images` — connector sync engine
  orchestrator (markdown + optional parsed base64 images)
- :func:`download_and_upload_crawl_images` — web-crawl orchestrator
  (crawl4ai ``media.images`` dicts)
- :func:`extract_markdown_image_urls`, :func:`is_valid_image_src`,
  :func:`resolve_relative_url`, :func:`dedupe_image_urls` — URL helpers
- :data:`MAX_IMAGE_SIZE`, :data:`MAX_IMAGES_PER_DOCUMENT`,
  :data:`PUBLIC_IMAGE_PATH_PREFIX` — size + URL invariants
"""

from klai_image_storage.pipeline import (
    download_and_upload_adapter_images,
    download_and_upload_crawl_images,
)
from klai_image_storage.storage import (
    MAX_IMAGE_SIZE,
    MAX_IMAGES_PER_DOCUMENT,
    PUBLIC_IMAGE_PATH_PREFIX,
    ImageStore,
    ImageUploadResult,
)
from klai_image_storage.utils import (
    dedupe_image_urls,
    extract_markdown_image_urls,
    is_valid_image_src,
    resolve_relative_url,
)

__all__ = [
    "MAX_IMAGES_PER_DOCUMENT",
    "MAX_IMAGE_SIZE",
    "PUBLIC_IMAGE_PATH_PREFIX",
    "ImageStore",
    "ImageUploadResult",
    "dedupe_image_urls",
    "download_and_upload_adapter_images",
    "download_and_upload_crawl_images",
    "extract_markdown_image_urls",
    "is_valid_image_src",
    "resolve_relative_url",
]
