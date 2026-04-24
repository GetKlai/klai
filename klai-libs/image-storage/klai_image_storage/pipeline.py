"""Download-and-upload orchestrators for the image pipeline.

SPEC-KB-IMAGE-002 — two orchestrators, one per consumer flow:

- :func:`download_and_upload_adapter_images` is used by the connector
  sync engine (github / notion / drive adapters). It accepts markdown
  ``(alt, url)`` tuples plus optional :class:`ParsedImage` entries for
  already-decoded images emitted by a document parser (e.g. Unstructured
  for PDF/DOCX).
- :func:`download_and_upload_crawl_images` is used by the web-crawl
  pipeline. It accepts a crawl4ai ``media.images`` list, filters
  Cloudflare srcset debris via :func:`is_valid_image_src`, resolves
  relative URLs against the page URL, and dedupes before downloading.

Both orchestrators return the list of public URLs so the caller can
append them to the Qdrant payload's ``image_urls`` field. Partial
failures log and do not abort — a single image's failure must never
halt a page or document ingest.

Internally both orchestrators share :func:`_download_validate_upload`:
GET → HTTP status check → size check → magic-byte validate → S3 upload.
Transport errors (``httpx.HTTPError``) log at ``warning``; upload-side
failures log at ``exception`` so unexpected crashes keep their stack.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from klai_image_storage.storage import (
    MAX_IMAGE_SIZE,
    MAX_IMAGES_PER_DOCUMENT,
)
from klai_image_storage.url_guard import (
    PinnedResolverTransport,
    SsrfBlockedError,
    validate_image_url,
)
from klai_image_storage.utils import (
    dedupe_image_urls,
    is_valid_image_src,
    resolve_relative_url,
)

if TYPE_CHECKING:
    from klai_image_storage.storage import ImageStore
    from klai_image_storage.types import ParsedImage

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


async def _download_validate_upload(
    url: str,
    *,
    http_client: httpx.AsyncClient,
    image_store: ImageStore,
    org_id: str,
    kb_slug: str,
    pin_transport: PinnedResolverTransport | None = None,
) -> str | None:
    """Fetch *url*, validate magic bytes, upload to S3, return the public URL.

    Returns the public URL on success, ``None`` on any failure. All
    failure paths log with the ``url`` field so production can correlate.

    Args:
        url: Image URL to fetch.
        http_client: httpx client that owns its own transport.
        image_store: S3 store client.
        org_id / kb_slug: Tenant context surfaced in log events.
        pin_transport: Optional :class:`PinnedResolverTransport` to
            seed with the validator's resolved IP before the GET.
            When provided, the fetch connects to the exact IP the
            guard accepted (closes the DNS-rebinding TOCTOU window,
            REQ-7.4). When ``None``, the guard has still run and
            the 60 s DNS cache narrows the window even without the
            transport.

    Exception policy:
    - :class:`SsrfBlockedError` (REQ-7.2) → ``warning`` with stable
      key ``adapter_image_ssrf_blocked`` and ``org_id``/``kb_slug``
      context. No HTTP request is made. A single image rejection
      never halts a document's ingest (AC-15).
    - ``httpx.HTTPError`` (connect, timeout, read, decode) → ``warning``,
      no traceback. These are expected when crawling hostile or dead
      pages.
    - any other ``Exception`` on the upload leg → ``exception``, with
      traceback. Unexpected — e.g. S3 layer crash, mock mis-set in tests.
    """
    # REQ-7.2 / AC-15 through AC-18: validate the image URL before ANY network
    # I/O. Runs before status checks, magic-byte validation, and the
    # http_client.get() call so docker-internal hosts are never probed
    # even for their TCP handshake.
    try:
        validated = await validate_image_url(url)
    except SsrfBlockedError as exc:
        logger.warning(
            "adapter_image_ssrf_blocked",
            url=url.split("?", 1)[0],
            hostname=exc.hostname,
            reason=exc.reason,
            org_id=org_id,
            kb_slug=kb_slug,
        )
        return None

    # REQ-7.4 / AC-23: seed the caller-provided transport's pin map
    # with the resolved IP so the GET targets the exact address the
    # guard accepted. Explicit kwarg is preferred over reaching into
    # http_client internals — the API contract is clear and httpx
    # version drift cannot silently disable pinning.
    if pin_transport is not None:
        pin_transport.pin(validated.hostname, validated.preferred_ip)

    try:
        resp = await http_client.get(url)
    except httpx.HTTPError as exc:
        # Expected transport failure (connect, timeout, decode, ...) — noisy
        # but normal when crawling hostile or dead sites. Warning without
        # traceback keeps VictoriaLogs readable.
        logger.warning("image_download_error", url=url, error=str(exc))
        return None
    except Exception:
        # Unexpected — e.g. a third-party interceptor, a mis-set test mock,
        # or an httpx internals bug. Keep the traceback so post-mortems work.
        logger.exception("image_download_unexpected_error", url=url)
        return None

    if resp.status_code != 200:
        logger.warning("image_download_failed", url=url, status=resp.status_code)
        return None

    data = resp.content
    if len(data) > MAX_IMAGE_SIZE:
        logger.warning("image_too_large", url=url, size=len(data))
        return None

    if not image_store.validate_image(data):
        logger.warning("image_invalid_content", url=url)
        return None

    try:
        result = await image_store.upload_image(
            org_id, kb_slug, data, _ext_from_url(url)
        )
    except Exception:
        logger.exception("image_upload_failed", url=url)
        return None

    return result.public_url


async def _upload_parsed_image(
    image: ParsedImage,
    *,
    image_store: ImageStore,
    org_id: str,
    kb_slug: str,
) -> str | None:
    """Validate + upload a pre-decoded :class:`ParsedImage`.

    Mirrors :func:`_download_validate_upload` minus the HTTP fetch leg.
    Logs include the image's ``source_id`` so failures are attributable
    to a specific parsed element (document path, Notion block ID, ...).
    """
    if len(image.data) > MAX_IMAGE_SIZE:
        logger.warning(
            "parsed_image_too_large", source_id=image.source_id, size=len(image.data)
        )
        return None

    if not image_store.validate_image(image.data):
        logger.warning("parsed_image_invalid_content", source_id=image.source_id)
        return None

    try:
        result = await image_store.upload_image(
            org_id, kb_slug, image.data, image.ext
        )
    except Exception:
        logger.exception("parsed_image_upload_failed", source_id=image.source_id)
        return None

    return result.public_url


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
    parsed_images: list[ParsedImage] | None = None,
    pin_transport: PinnedResolverTransport | None = None,
) -> list[str]:
    """Upload images for a connector-adapter document.

    Args:
        image_urls: ``(alt, url)`` tuples extracted from markdown. Alts
            are not used for storage — they only document intent.
        org_id: Organisation ID for tenant-scoped storage.
        kb_slug: Knowledge base slug.
        image_store: S3 image store client.
        http_client: Async HTTP client for downloading URL-referenced
            images. The caller owns its lifecycle.
        parsed_images: Optional list of already-decoded images from a
            document parser (e.g. Unstructured for PDF/DOCX). The
            caller is responsible for the base64 decode and the
            MIME-to-extension mapping; see :class:`ParsedImage`.
        pin_transport: Optional :class:`PinnedResolverTransport` paired
            with *http_client* — when provided, the SSRF guard's
            resolved IP is registered so the actual GET connects to
            that exact address. See REQ-7.4 / AC-23.

    Returns:
        List of public URLs for successfully uploaded images, in the
        order they were processed (parsed first, then URL-referenced).
    """
    uploaded_urls: list[str] = []
    remaining = MAX_IMAGES_PER_DOCUMENT

    for image in parsed_images or []:
        if remaining <= 0:
            break
        public_url = await _upload_parsed_image(
            image, image_store=image_store, org_id=org_id, kb_slug=kb_slug
        )
        if public_url is not None:
            uploaded_urls.append(public_url)
            remaining -= 1

    for _alt, url in image_urls:
        if remaining <= 0:
            break
        public_url = await _download_validate_upload(
            url,
            http_client=http_client,
            image_store=image_store,
            org_id=org_id,
            kb_slug=kb_slug,
            pin_transport=pin_transport,
        )
        if public_url is not None:
            uploaded_urls.append(public_url)
            remaining -= 1

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
    pin_transport: PinnedResolverTransport | None = None,
) -> list[str]:
    """Upload images for a web-crawled page.

    Filters crawl4ai ``media.images`` entries, resolves relative URLs
    against *base_url*, dedupes by final resolved URL, and then feeds
    each through :func:`_download_validate_upload`.

    Args:
        media_images: ``result.media.images`` from crawl4ai (list of
            dicts with ``src`` / ``data_src`` / ``alt``).
        base_url: Page URL — used to resolve relative image paths.
        org_id: Tenant org_id for the S3 key namespace.
        kb_slug: Knowledge base slug.
        image_store: S3 client to upload into.
        http_client: Async HTTP client used for image downloads. The
            caller owns its lifecycle.
        pin_transport: Optional :class:`PinnedResolverTransport` paired
            with *http_client* — see the adapter orchestrator docs
            for the REQ-7.4 / AC-23 rationale.

    Returns:
        List of public URLs for successfully uploaded images, in order
        of first appearance, deduplicated by final resolved URL.
    """
    pairs = _collect_srcs(media_images)
    if not pairs:
        return []

    resolved: list[str] = [resolve_relative_url(src, base_url) for _alt, src in pairs]
    urls_to_fetch = dedupe_image_urls(resolved)

    uploaded_urls: list[str] = []
    remaining = MAX_IMAGES_PER_DOCUMENT

    for url in urls_to_fetch:
        if remaining <= 0:
            break
        public_url = await _download_validate_upload(
            url,
            http_client=http_client,
            image_store=image_store,
            org_id=org_id,
            kb_slug=kb_slug,
            pin_transport=pin_transport,
        )
        if public_url is not None:
            uploaded_urls.append(public_url)
            remaining -= 1

    return uploaded_urls
