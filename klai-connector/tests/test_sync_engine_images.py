"""Tests for image handling in the SyncEngine."""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.sync_images import download_and_upload_images


class TestDownloadAndUploadImages:
    """Tests for the image download + upload orchestration function."""

    @pytest.mark.asyncio
    async def test_uploads_markdown_images(self):
        """Markdown image URLs are downloaded and uploaded to S3."""
        mock_store = MagicMock()
        mock_store.validate_image = MagicMock(return_value="image/png")

        async def fake_upload(*args, **kwargs):
            from app.services.s3_storage import ImageUploadResult
            return ImageUploadResult(
                object_key="org/img/hash.png", public_url="/kb-images/org/img/hash.png", deduplicated=False,
            )

        mock_store.upload_image = fake_upload

        # PNG magic bytes for validation
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = png_bytes
        mock_http.get = AsyncMock(return_value=mock_response)

        result = await download_and_upload_images(
            image_urls=[("logo", "https://example.com/logo.png")],
            org_id="org-1",
            kb_slug="kb-1",
            image_store=mock_store,
            http_client=mock_http,
        )

        assert len(result) == 1
        assert result[0] == "/kb-images/org/img/hash.png"

    @pytest.mark.asyncio
    async def test_skips_failed_downloads(self):
        """Failed image downloads are logged and skipped."""
        mock_store = MagicMock()
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=Exception("Connection refused"))

        result = await download_and_upload_images(
            image_urls=[("img", "https://broken.com/img.png")],
            org_id="org-1",
            kb_slug="kb-1",
            image_store=mock_store,
            http_client=mock_http,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_skips_non_image_content(self):
        """Downloaded content that isn't a valid image is skipped."""
        mock_store = MagicMock()
        mock_store.validate_image = MagicMock(return_value=None)
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"<html>Not an image</html>"
        mock_http.get = AsyncMock(return_value=mock_response)

        result = await download_and_upload_images(
            image_urls=[("fake", "https://example.com/page.html")],
            org_id="org-1",
            kb_slug="kb-1",
            image_store=mock_store,
            http_client=mock_http,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_skips_oversized_images(self):
        """Images larger than 5 MB are skipped."""
        mock_store = MagicMock()
        mock_store.validate_image = MagicMock(return_value="image/png")
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\x89PNG" + b"\x00" * (6 * 1024 * 1024)  # 6 MB
        mock_http.get = AsyncMock(return_value=mock_response)

        result = await download_and_upload_images(
            image_urls=[("big", "https://example.com/huge.png")],
            org_id="org-1",
            kb_slug="kb-1",
            image_store=mock_store,
            http_client=mock_http,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_respects_max_images_limit(self):
        """Only the first MAX_IMAGES_PER_DOCUMENT images are processed."""
        mock_store = MagicMock()
        mock_store.validate_image = MagicMock(return_value="image/png")

        async def fake_upload(*args, **kwargs):
            from app.services.s3_storage import ImageUploadResult
            return ImageUploadResult(object_key="key", public_url="/kb-images/key", deduplicated=False)

        mock_store.upload_image = fake_upload

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mock_http = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = png_bytes
        mock_http.get = AsyncMock(return_value=mock_response)

        # 25 images, but limit is 20
        urls = [(f"img{i}", f"https://example.com/img{i}.png") for i in range(25)]

        result = await download_and_upload_images(
            image_urls=urls,
            org_id="org-1",
            kb_slug="kb-1",
            image_store=mock_store,
            http_client=mock_http,
        )

        assert len(result) == 20

    @pytest.mark.asyncio
    async def test_handles_parsed_images_b64(self):
        """Base64 images from parser (PDF/DOCX) are uploaded directly."""
        mock_store = MagicMock()
        mock_store.validate_image = MagicMock(return_value="image/png")

        async def fake_upload(*args, **kwargs):
            from app.services.s3_storage import ImageUploadResult
            return ImageUploadResult(object_key="key", public_url="/kb-images/key", deduplicated=False)

        mock_store.upload_image = fake_upload

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        b64_data = base64.b64encode(png_bytes).decode()

        result = await download_and_upload_images(
            image_urls=[],
            org_id="org-1",
            kb_slug="kb-1",
            image_store=mock_store,
            http_client=AsyncMock(),
            parsed_images=[{"data_b64": b64_data, "mime_type": "image/png"}],
        )

        assert len(result) == 1
        assert result[0] == "/kb-images/key"
