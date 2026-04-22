"""Async S3 image storage client backed by the minio SDK.

SPEC-KB-IMAGE-002 — ported verbatim from
``klai-knowledge-ingest/knowledge_ingest/s3_storage.py`` (which itself
was a straight port of ``klai-connector/app/services/s3_storage.py``).
The store wraps the synchronous minio client with :func:`asyncio.to_thread`
so the embedding service's event loop (FastAPI endpoints, Procrastinate
workers, connector sync tasks) stays non-blocking. Images are uploaded
authenticated over the S3 API (Garage on :3900) and served anonymously
via Garage website mode through a Caddy reverse proxy at
``/kb-images/{object_key}``.

Content-addressed keys (SHA-256 of bytes) give free deduplication across
tenants' own KBs. The key format + URL prefix are wire-level contracts;
changing either breaks every previously uploaded image's public URL.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
from dataclasses import dataclass

import filetype  # type: ignore[import-untyped]
import structlog
from minio import Minio
from minio.error import S3Error

logger = structlog.get_logger()

# Image MIME types we accept (validated via magic bytes, not extension).
_ALLOWED_IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)

# SVG has no magic bytes that filetype recognises, so we check manually.
_SVG_SIGNATURES = (b"<?xml", b"<svg")

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_IMAGES_PER_DOCUMENT = 20

# Public URL prefix served by Caddy → Garage website endpoint.
# The full URL becomes: https://{tenant}.getklai.com/kb-images/{object_key}
PUBLIC_IMAGE_PATH_PREFIX = "/kb-images"


@dataclass(frozen=True)
class ImageUploadResult:
    """Result of an image upload operation."""

    object_key: str
    public_url: str
    deduplicated: bool


# @MX:ANCHOR: ImageStore — single content-addressed image storage boundary
#   for every Klai service that uploads to Garage.
# @MX:REASON: Every connector sync and every crawl page calls upload_image().
#   Changing build_object_key or PUBLIC_IMAGE_PATH_PREFIX breaks every
#   previously uploaded image's public URL.
# @MX:SPEC: SPEC-KB-IMAGE-001, SPEC-KB-IMAGE-002, SPEC-CRAWLER-004
class ImageStore:
    """Tenant-scoped, content-addressed image storage over S3."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "garage",
        **_kwargs: object,
    ) -> None:
        self._bucket = bucket
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            secure=False,
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @staticmethod
    def build_object_key(org_id: str, kb_slug: str, data: bytes, ext: str) -> str:
        """Build a content-addressed S3 object key.

        Format: ``{org_id}/images/{kb_slug}/{sha256}.{ext}``
        """
        content_hash = hashlib.sha256(data).hexdigest()
        ext = ext.lower().lstrip(".")
        return f"{org_id}/images/{kb_slug}/{content_hash}.{ext}"

    @staticmethod
    def build_public_url(object_key: str) -> str:
        """Build the relative public URL for an image served via Caddy."""
        return f"{PUBLIC_IMAGE_PATH_PREFIX}/{object_key}"

    @staticmethod
    def validate_image(data: bytes) -> str | None:
        """Return MIME type if *data* looks like a supported image, else ``None``.

        Uses magic-byte detection via the ``filetype`` library. SVG is
        checked separately (text format with no reliable binary magic).
        """
        if not data:
            return None

        header = data[:256].lstrip()
        if any(header.startswith(sig) for sig in _SVG_SIGNATURES):
            return "image/svg+xml"

        kind = filetype.guess(data[:261])  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        if kind is None:
            return None
        mime: str = kind.mime  # pyright: ignore[reportUnknownMemberType]
        if mime in _ALLOWED_IMAGE_MIMES:
            return mime
        return None

    # ------------------------------------------------------------------
    # Async S3 operations (minio is sync → asyncio.to_thread)
    # ------------------------------------------------------------------

    async def upload_image(
        self,
        org_id: str,
        kb_slug: str,
        data: bytes,
        ext: str,
    ) -> ImageUploadResult:
        """Upload an image with content-addressed deduplication.

        If an object with the same SHA-256 key already exists, the upload
        is skipped and the public URL is returned directly.
        """
        object_key = self.build_object_key(org_id, kb_slug, data, ext)

        if await self._object_exists(object_key):
            logger.info("image_deduplicated", object_key=object_key)
            return ImageUploadResult(
                object_key=object_key,
                public_url=self.build_public_url(object_key),
                deduplicated=True,
            )

        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            object_key,
            io.BytesIO(data),
            len(data),
            content_type=self.validate_image(data) or "application/octet-stream",
        )
        logger.info("image_uploaded", object_key=object_key, size=len(data))
        return ImageUploadResult(
            object_key=object_key,
            public_url=self.build_public_url(object_key),
            deduplicated=False,
        )

    async def _object_exists(self, object_key: str) -> bool:
        """Check if an object exists in the bucket."""
        try:
            await asyncio.to_thread(self._client.stat_object, self._bucket, object_key)
            return True
        except S3Error:
            return False
