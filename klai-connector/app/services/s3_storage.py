"""Async S3 image storage client backed by the minio SDK.

Uses ``asyncio.to_thread()`` to wrap the synchronous minio client,
keeping the connector's async event loop non-blocking.  Designed for
Garage (S3-compatible) but works with any S3-compatible store.

Images are served publicly via Garage's website mode + Caddy reverse proxy.
No presigned URLs needed — Caddy handles TLS, Garage serves anonymously.
"""

import asyncio
import hashlib
import io
from dataclasses import dataclass

import filetype
from minio import Minio
from minio.error import S3Error

from app.core.logging import get_logger

logger = get_logger(__name__)

# Image MIME types we accept (validated via magic bytes, not extension).
_ALLOWED_IMAGE_MIMES: frozenset[str] = frozenset({
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
})

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


class ImageStore:
    """Tenant-scoped, content-addressed image storage over S3.

    Images are uploaded via S3 API (authenticated) and served publicly via
    Garage's website mode through a Caddy reverse proxy at ``/kb-images/``.

    Args:
        endpoint: S3 endpoint (e.g. ``garage:3900``).
        access_key: S3 access key.
        secret_key: S3 secret key.
        bucket: Target bucket name.
        region: S3 region (must be ``"garage"`` for Garage).
    """

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
        """Build the public URL path for an image served via Caddy.

        Returns a relative path like ``/kb-images/{object_key}`` that is
        resolved by the frontend against the current tenant domain.
        """
        return f"{PUBLIC_IMAGE_PATH_PREFIX}/{object_key}"

    @staticmethod
    def validate_image(data: bytes) -> str | None:
        """Return MIME type if *data* looks like a supported image, else ``None``.

        Uses magic-byte detection via the ``filetype`` library.
        SVG is checked separately (no reliable magic bytes).
        """
        if not data:
            return None

        # SVG check (text-based, filetype can't detect it).
        header = data[:256].lstrip()
        if any(header.startswith(sig) for sig in _SVG_SIGNATURES):
            return "image/svg+xml"

        kind = filetype.guess(data[:261])
        if kind and kind.mime in _ALLOWED_IMAGE_MIMES:
            return kind.mime

        return None

    # ------------------------------------------------------------------
    # Async S3 operations (minio is sync → asyncio.to_thread)
    # ------------------------------------------------------------------

    async def upload_image(
        self, org_id: str, kb_slug: str, data: bytes, ext: str,
    ) -> ImageUploadResult:
        """Upload an image to S3 with content-addressed deduplication.

        If an object with the same SHA-256 key already exists, the upload
        is skipped and the public URL is returned directly.
        """
        object_key = self.build_object_key(org_id, kb_slug, data, ext)

        # Check for existing object (deduplication).
        if await self._object_exists(object_key):
            logger.info("Image deduplicated: %s", object_key)
            return ImageUploadResult(
                object_key=object_key,
                public_url=self.build_public_url(object_key),
                deduplicated=True,
            )

        # Upload new object.
        await asyncio.to_thread(
            self._client.put_object,
            self._bucket,
            object_key,
            io.BytesIO(data),
            len(data),
            content_type=self.validate_image(data) or "application/octet-stream",
        )
        logger.info("Image uploaded: %s (%d bytes)", object_key, len(data))
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
