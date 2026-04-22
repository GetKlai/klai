"""Download-and-upload orchestrators for the image pipeline.

SPEC-KB-IMAGE-002 — two orchestrators, one per consumer flow:

- :func:`download_and_upload_adapter_images` is used by the connector
  sync engine (github / notion / drive adapters). It accepts markdown
  ``(alt, url)`` tuples plus optional base64-encoded parser images
  emitted by Unstructured for PDF/DOCX.
- :func:`download_and_upload_crawl_images` is used by the web-crawl
  pipeline. It accepts a crawl4ai ``media.images`` list, filters
  Cloudflare srcset debris via :func:`is_valid_image_src`, resolves
  relative URLs against the page URL, and dedupes before downloading.

Both orchestrators return the list of public URLs so the caller can
append them to the Qdrant payload's ``image_urls`` field (it flows
through via the SPEC-KB-021 extra passthrough rule). Partial failures
log at ``warning`` with the source URL and do not abort — single-image
errors must not halt a page or document ingest.
"""

from __future__ import annotations

import base64
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from klai_image_storage.storage import (
    MAX_IMAGE_SIZE,
    MAX_IMAGES_PER_DOCUMENT,
)
from klai_image_storage.utils import (
    dedupe_image_urls,
    is_valid_image_src,
    resolve_relative_url,
)

if TYPE_CHECKING:
    from klai_image_storage.storage import ImageStore

logger = structlog.get_logger()


def _ext_from_url(url: str) -> str:
    """Extract a file extension from a URL path, defaulting to ``png``."""
    suffix = PurePosixPath(url.split("?")[0]).suffix.lower().lstrip(".")
    return suffix if suffix else "png"


def _ext_from_mime(mime: str) -> str:
    """Map a MIME type to a file extension."""
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
        "image/svg+xml": "svg",
    }.get(mime, "png")


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


# @MX:ANCHOR: download_and_upload_adapter_images — connector sync-engine hot path.
# @MX:REASON: Every github/notion/drive sync calls this for every document.
#   A silent upload failure here strands images for the affected KB.
# @MX:SPEC: SPEC-KB-IMAGE-002
async def download_and_upload_adapter_images(
    *,
    image_urls: list[tuple[str, str]],
    org_id: str,
    kb_slug: str,
    image_store: ImageStore,
    http_client: httpx.AsyncClient,
    parsed_images: list[dict[str, str]] | None = None,
) -> list[str]:
    """Download images from URLs and upload to S3 (connector adapter path).

    Args:
        image_urls: List of ``(alt, url)`` tuples from markdown extraction.
        org_id: Organisation ID for tenant-scoped storage.
        kb_slug: Knowledge base slug.
        image_store: S3 image store client.
        http_client: Async HTTP client for downloading images. The caller
            owns its lifecycle.
        parsed_images: Optional list of base64-encoded images from the
            parser (extracted from PDF/DOCX via Unstructured).

    Returns:
        List of public URLs for successfully uploaded images.
    """
    uploaded_urls: list[str] = []
    remaining = MAX_IMAGES_PER_DOCUMENT

    # Phase 1: Upload base64 images from parser (PDF/DOCX).
    for img in parsed_images or []:
        if remaining <= 0:
            break
        try:
            data = base64.b64decode(img["data_b64"])
            mime = img.get("mime_type", "image/png")
            if not image_store.validate_image(data):
                continue
            if len(data) > MAX_IMAGE_SIZE:
                logger.warning("parsed_image_too_large", size=len(data))
                continue
            result = await image_store.upload_image(
                org_id, kb_slug, data, _ext_from_mime(mime)
            )
            uploaded_urls.append(result.public_url)
            remaining -= 1
        except Exception:
            logger.exception("parsed_image_upload_failed")

    # Phase 2: Download and upload URL-referenced images.
    # @MX:NOTE: Broad ``except Exception`` matches pre-SPEC klai-connector
    #   behaviour exactly (zero-behaviour-change, SPEC-KB-IMAGE-002 REQ-03).
    #   A single image's failure must never abort the sync.
    for _alt, url in image_urls:
        if remaining <= 0:
            break
        try:
            resp = await http_client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    "image_download_failed", url=url, status=resp.status_code
                )
                continue

            data = resp.content
            if len(data) > MAX_IMAGE_SIZE:
                logger.warning("image_too_large", url=url, size=len(data))
                continue

            if not image_store.validate_image(data):
                logger.warning("image_invalid_content", url=url)
                continue

            result = await image_store.upload_image(
                org_id, kb_slug, data, _ext_from_url(url)
            )
            uploaded_urls.append(result.public_url)
            remaining -= 1
        except Exception:
            logger.exception("image_download_upload_failed", url=url)

    return uploaded_urls


# @MX:ANCHOR: download_and_upload_crawl_images — web-crawl pipeline hot path.
# @MX:REASON: Every crawled page with media.images passes through this; any
#   upload failure strands images for that page. Partial failures are
#   intentional (one image's 404 must not kill the page).
# @MX:SPEC: SPEC-KB-IMAGE-002, SPEC-CRAWLER-004
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
            result = await image_store.upload_image(
                org_id, kb_slug, data, _ext_from_url(url)
            )
        except Exception:
            logger.exception("image_upload_failed", url=url)
            continue

        uploaded_urls.append(result.public_url)
        remaining -= 1

    return uploaded_urls
