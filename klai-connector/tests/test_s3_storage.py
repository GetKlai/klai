"""Tests for S3 image storage client."""

import hashlib
from unittest.mock import MagicMock

import pytest

from app.services.s3_storage import ImageStore, ImageUploadResult


class TestImageStore:
    """Tests for the ImageStore S3 client wrapper."""

    def _make_store(self, **kwargs) -> ImageStore:
        return ImageStore(
            endpoint=kwargs.get("endpoint", "garage:3900"),
            access_key=kwargs.get("access_key", "test-key"),
            secret_key=kwargs.get("secret_key", "test-secret"),
            bucket=kwargs.get("bucket", "klai-images"),
            region=kwargs.get("region", "garage"),
            presigned_ttl_seconds=kwargs.get("presigned_ttl_seconds", 604800),
        )

    def test_object_key_is_content_addressed(self):
        """Object key uses SHA256 hash for deduplication."""
        store = self._make_store()
        data = b"fake image data"
        expected_hash = hashlib.sha256(data).hexdigest()
        key = store.build_object_key("org-123", "my-kb", data, ".png")
        assert key == f"org-123/images/my-kb/{expected_hash}.png"

    def test_object_key_normalises_extension(self):
        """Extension is lowercased and always starts with a dot."""
        store = self._make_store()
        data = b"x"
        key = store.build_object_key("org-1", "kb", data, "PNG")
        assert key.endswith(".png")

    def test_object_key_strips_leading_dot(self):
        """Double-dot is prevented when caller passes '.png'."""
        store = self._make_store()
        data = b"x"
        key = store.build_object_key("org-1", "kb", data, ".png")
        assert ".." not in key
        assert key.endswith(".png")

    @pytest.mark.asyncio
    async def test_upload_image_calls_put_object(self):
        """upload_image puts the object and returns a presigned URL."""
        from minio.error import S3Error

        store = self._make_store()
        mock_client = MagicMock()
        mock_client.stat_object = MagicMock(
            side_effect=S3Error("NoSuchKey", "Not found", "", "", "", "")
        )
        mock_client.put_object = MagicMock()
        mock_client.presigned_get_object = MagicMock(return_value="https://garage:3900/signed-url")
        store._client = mock_client

        result = await store.upload_image("org-1", "kb-1", b"PNG image bytes", ".png")

        assert isinstance(result, ImageUploadResult)
        assert result.presigned_url == "https://garage:3900/signed-url"
        assert result.object_key.startswith("org-1/images/kb-1/")
        mock_client.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_image_deduplicates(self):
        """upload_image skips upload when object already exists."""
        store = self._make_store()
        mock_client = MagicMock()
        # stat_object succeeds = object exists
        mock_client.stat_object = MagicMock(return_value=MagicMock())
        mock_client.presigned_get_object = MagicMock(return_value="https://garage:3900/existing")
        store._client = mock_client

        result = await store.upload_image("org-1", "kb-1", b"data", ".jpg")

        assert result.presigned_url == "https://garage:3900/existing"
        assert result.deduplicated is True
        mock_client.put_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_image_handles_missing_object(self):
        """upload_image uploads when stat_object raises S3Error (not found)."""
        from minio.error import S3Error

        store = self._make_store()
        mock_client = MagicMock()
        mock_client.stat_object = MagicMock(
            side_effect=S3Error("NoSuchKey", "Not found", "", "", "", "")
        )
        mock_client.put_object = MagicMock()
        mock_client.presigned_get_object = MagicMock(return_value="https://garage:3900/new")
        store._client = mock_client

        result = await store.upload_image("org-1", "kb-1", b"new data", ".png")

        assert result.deduplicated is False
        mock_client.put_object.assert_called_once()

    def test_validate_image_accepts_png(self):
        """PNG magic bytes are accepted."""
        store = self._make_store()
        # PNG magic bytes
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        result = store.validate_image(png_header)
        assert result is not None
        assert result.startswith("image/")

    def test_validate_image_accepts_jpeg(self):
        """JPEG magic bytes are accepted."""
        store = self._make_store()
        jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        result = store.validate_image(jpeg_header)
        assert result is not None
        assert result.startswith("image/")

    def test_validate_image_rejects_text(self):
        """Plain text is rejected."""
        store = self._make_store()
        assert store.validate_image(b"Hello, world!") is None

    def test_validate_image_rejects_empty(self):
        """Empty bytes are rejected."""
        store = self._make_store()
        assert store.validate_image(b"") is None

    def test_validate_image_accepts_gif(self):
        """GIF magic bytes are accepted."""
        store = self._make_store()
        gif_header = b"GIF89a" + b"\x00" * 100
        result = store.validate_image(gif_header)
        assert result is not None

    def test_validate_image_accepts_webp(self):
        """WebP magic bytes are accepted."""
        store = self._make_store()
        # Minimal valid RIFF/WEBP header: RIFF + size (4 bytes LE) + WEBP + VP8 chunk
        webp_header = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x18\x00\x00\x00" + b"\x00" * 24
        result = store.validate_image(webp_header)
        assert result is not None
