"""Image download, validation, and upload orchestration for the sync engine."""

from __future__ import annotations

import base64
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.services.s3_storage import MAX_IMAGE_SIZE, MAX_IMAGES_PER_DOCUMENT

if TYPE_CHECKING:
    import httpx

    from app.services.s3_storage import ImageStore

logger = get_logger(__name__)


def _ext_from_url(url: str) -> str:
    """Extract file extension from a URL path, defaulting to ``png``."""
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


async def download_and_upload_images(
    *,
    image_urls: list[tuple[str, str]],
    org_id: str,
    kb_slug: str,
    image_store: ImageStore,
    http_client: httpx.AsyncClient,
    parsed_images: list[dict[str, str]] | None = None,
) -> list[str]:
    """Download images from URLs and upload to S3.

    Args:
        image_urls: List of ``(alt, url)`` tuples from markdown extraction.
        org_id: Organisation ID for tenant-scoped storage.
        kb_slug: Knowledge base slug.
        image_store: S3 image store client.
        http_client: Async HTTP client for downloading images.
        parsed_images: Optional list of base64-encoded images from the parser
            (extracted from PDF/DOCX via Unstructured).

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
                logger.warning("Parsed image too large (%d bytes), skipping", len(data))
                continue
            result = await image_store.upload_image(org_id, kb_slug, data, _ext_from_mime(mime))
            uploaded_urls.append(result.public_url)
            remaining -= 1
        except Exception:
            logger.exception("Failed to upload parsed image")

    # Phase 2: Download and upload URL-referenced images.
    for _alt, url in image_urls:
        if remaining <= 0:
            break
        try:
            resp = await http_client.get(url)
            if resp.status_code != 200:
                logger.warning("Image download failed: %s (HTTP %d)", url, resp.status_code)
                continue

            data = resp.content
            if len(data) > MAX_IMAGE_SIZE:
                logger.warning("Image too large (%d bytes), skipping: %s", len(data), url)
                continue

            if not image_store.validate_image(data):
                logger.warning("Downloaded content is not a valid image: %s", url)
                continue

            ext = _ext_from_url(url)
            result = await image_store.upload_image(org_id, kb_slug, data, ext)
            uploaded_urls.append(result.public_url)
            remaining -= 1
        except Exception:
            logger.exception("Failed to download/upload image: %s", url)

    return uploaded_urls
