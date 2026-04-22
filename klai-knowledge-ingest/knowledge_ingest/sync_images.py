"""Download-and-upload orchestration for crawl-pipeline images.

SPEC-CRAWLER-004 Fase A — takes a crawl4ai ``media.images`` list (dicts
with ``src``/``alt``), filters Cloudflare srcset debris via
``is_valid_image_src``, resolves relative URLs against the page URL,
dedupes, downloads each candidate, validates via magic bytes, and
uploads to Garage. Returns the list of public URLs so the caller can
append them to the Qdrant payload's ``image_urls`` field (they flow
through via the SPEC-KB-021 extra passthrough rule).

Partial failures are logged at ``warning`` with the source URL and do
not abort the page — REQ-02.4 (HTTP 4xx/5xx on a single image must not
halt the sync).
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from knowledge_ingest.image_utils import (
    dedupe_image_urls,
    is_valid_image_src,
    resolve_relative_url,
)
from knowledge_ingest.s3_storage import (
    MAX_IMAGE_SIZE,
    MAX_IMAGES_PER_DOCUMENT,
)

if TYPE_CHECKING:
    from knowledge_ingest.s3_storage import ImageStore

logger = structlog.get_logger()


def _ext_from_url(url: str) -> str:
    """Extract a file extension from a URL path, defaulting to ``png``."""
    suffix = PurePosixPath(url.split("?")[0]).suffix.lower().lstrip(".")
    return suffix if suffix else "png"


def _collect_srcs(media_images: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Return ``[(alt, src)]`` tuples from a crawl4ai ``media.images`` list.

    Filters out srcset debris and data-URIs. Order follows the crawl4ai
    response so downstream deduplication is deterministic.
    """
    pairs: list[tuple[str, str]] = []
    for img in media_images:
        raw_src = img.get("src") or img.get("data_src") or ""
        if not is_valid_image_src(raw_src):
            continue
        alt = img.get("alt") or ""
        pairs.append((alt, raw_src.strip()))
    return pairs


async def download_and_upload_crawl_images(
    *,
    media_images: list[dict[str, Any]],
    base_url: str,
    org_id: str,
    kb_slug: str,
    image_store: ImageStore,
    http_client: httpx.AsyncClient,
) -> list[str]:
    """Download, validate and upload images from a crawl4ai ``media.images`` list.

    Args:
        media_images: ``result.media.images`` from crawl4ai (list of dicts).
        base_url: Page URL — used to resolve relative image paths.
        org_id: Tenant org_id for the S3 key namespace.
        kb_slug: Knowledge base slug.
        image_store: S3 client to upload into.
        http_client: Async HTTP client used for image downloads. The caller
            owns its lifecycle.

    Returns:
        List of public URLs for successfully uploaded images, in order of
        first appearance, deduplicated by final resolved URL.
    """
    pairs = _collect_srcs(media_images)
    if not pairs:
        return []

    # Resolve against base_url + dedupe by final URL, preserving order.
    resolved: list[str] = [resolve_relative_url(src, base_url) for _alt, src in pairs]
    urls_to_fetch = dedupe_image_urls(resolved)

    uploaded_urls: list[str] = []
    remaining = MAX_IMAGES_PER_DOCUMENT

    for url in urls_to_fetch:
        if remaining <= 0:
            break
        try:
            resp = await http_client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("image_download_error", url=url, error=str(exc))
            continue

        if resp.status_code != 200:
            logger.warning("image_download_failed", url=url, status=resp.status_code)
            continue

        data = resp.content
        if len(data) > MAX_IMAGE_SIZE:
            logger.warning("image_too_large", url=url, size=len(data))
            continue

        if not image_store.validate_image(data):
            logger.warning("image_invalid_content", url=url)
            continue

        try:
            result = await image_store.upload_image(org_id, kb_slug, data, _ext_from_url(url))
        except Exception:
            logger.exception("image_upload_failed", url=url)
            continue

        uploaded_urls.append(result.public_url)
        remaining -= 1

    return uploaded_urls
