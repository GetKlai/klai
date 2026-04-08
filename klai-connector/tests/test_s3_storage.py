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
        )

    def test_object_key_is_content_addressed(self):
        """Object key uses SHA256 hash for deduplication."""
        store = self._make_store()
        data = b"fake image data"
        expected_hash = hashlib.sha256(data).hexdigest()
        key = store.build_object_key("org-123", "my-kb", data, ".png")
        assert key == f"org-123/images/my-kb/{expected_hash}.png"

    def test_object_key_normalises_extension(self):
        store = self._make_store()
        key = store.build_object_key("org-1", "kb", b"x", "PNG")
        assert key.endswith(".png")

    def test_object_key_strips_leading_dot(self):
        store = self._make_store()
        key = store.build_object_key("org-1", "kb", b"x", ".png")
        assert ".." not in key
        assert key.endswith(".png")

    def test_public_url_format(self):
        """Public URL uses /kb-images/ prefix."""
        url = ImageStore.build_public_url("org-1/images/kb/abc123.png")
        assert url == "/kb-images/org-1/images/kb/abc123.png"

    @pytest.mark.asyncio
    async def test_upload_image_calls_put_object(self):
        """upload_image puts the object and returns a public URL."""
        from minio.error import S3Error

        store = self._make_store()
        mock_client = MagicMock()
        mock_client.stat_object = MagicMock(
            side_effect=S3Error("NoSuchKey", "Not found", "", "", "", "")
        )
        mock_client.put_object = MagicMock()
        store._client = mock_client

        result = await store.upload_image("org-1", "kb-1", b"PNG image bytes", ".png")

        assert isinstance(result, ImageUploadResult)
        assert result.public_url.startswith("/kb-images/org-1/images/kb-1/")
        assert result.object_key.startswith("org-1/images/kb-1/")
        mock_client.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_image_deduplicates(self):
        """upload_image skips upload when object already exists."""
        store = self._make_store()
        mock_client = MagicMock()
        mock_client.stat_object = MagicMock(return_value=MagicMock())
        store._client = mock_client

        result = await store.upload_image("org-1", "kb-1", b"data", ".jpg")

        assert result.public_url.startswith("/kb-images/")
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
        store._client = mock_client

        result = await store.upload_image("org-1", "kb-1", b"new data", ".png")

        assert result.deduplicated is False
        mock_client.put_object.assert_called_once()

    def test_validate_image_accepts_png(self):
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert ImageStore.validate_image(png_header) is not None

    def test_validate_image_accepts_jpeg(self):
        jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        assert ImageStore.validate_image(jpeg_header) is not None

    def test_validate_image_rejects_text(self):
        assert ImageStore.validate_image(b"Hello, world!") is None

    def test_validate_image_rejects_empty(self):
        assert ImageStore.validate_image(b"") is None

    def test_validate_image_accepts_gif(self):
        gif_header = b"GIF89a" + b"\x00" * 100
        assert ImageStore.validate_image(gif_header) is not None

    def test_validate_image_accepts_webp(self):
        webp_header = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x18\x00\x00\x00" + b"\x00" * 24
        assert ImageStore.validate_image(webp_header) is not None
