"""Tests for image handling in the SyncEngine."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from klai_image_storage import (
    ParsedImage,
)
from klai_image_storage import (
    download_and_upload_adapter_images as download_and_upload_images,
)


class TestDownloadAndUploadImages:
    """Tests for the image download + upload orchestration function."""

    @pytest.mark.asyncio
    async def test_uploads_markdown_images(self):
        """Markdown image URLs are downloaded and uploaded to S3."""
        mock_store = MagicMock()
        mock_store.validate_image = MagicMock(return_value="image/png")

        async def fake_upload(*args, **kwargs):
            from klai_image_storage import ImageUploadResult
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
            from klai_image_storage import ImageUploadResult
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
            from klai_image_storage import ImageUploadResult
            return ImageUploadResult(object_key="key", public_url="/kb-images/key", deduplicated=False)

        mock_store.upload_image = fake_upload

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        result = await download_and_upload_images(
            image_urls=[],
            org_id="org-1",
            kb_slug="kb-1",
            image_store=mock_store,
            http_client=AsyncMock(),
            parsed_images=[ParsedImage(data=png_bytes, ext="png")],
        )

        assert len(result) == 1
        assert result[0] == "/kb-images/key"


class TestUploadImagesIsConnectorAgnostic:
    """SyncEngine._upload_images reads only ref.images — no connector-type dispatch."""

    @pytest.mark.asyncio
    async def test_uploads_ref_images_without_knowing_connector_type(self):
        """Adapter-provided ref.images (absolute URLs) are uploaded as-is."""
        from types import SimpleNamespace

        from app.adapters.base import ImageRef
        from app.services.sync_engine import SyncEngine

        # Minimal SyncEngine stubs — we only exercise _upload_images.
        engine = SyncEngine.__new__(SyncEngine)
        engine._image_store = MagicMock()
        engine._image_store.validate_image = MagicMock(return_value="image/png")

        async def fake_upload(*args, **kwargs):
            from klai_image_storage import ImageUploadResult
            return ImageUploadResult(
                object_key="k", public_url="/kb-images/k", deduplicated=False,
            )

        engine._image_store.upload_image = fake_upload

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        mock_http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.content = png_bytes
        mock_http.get = AsyncMock(return_value=resp)
        engine._image_http = mock_http

        ref = SimpleNamespace(
            path="docs/guide.md",
            source_url="https://github.com/acme/docs/blob/main/docs/guide.md",
            images=[
                ImageRef(url="https://cdn.example.com/logo.png", alt="logo", source_path=""),
                ImageRef(url="https://cdn.example.com/icon.png", alt="icon", source_path=""),
            ],
        )

        result = await engine._upload_images(
            parsed_images=[],
            ref=ref,
            org_id="org-1",
            kb_slug="kb-1",
        )

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_images(self):
        """ref.images=None and no parsed_images means nothing to upload."""
        from types import SimpleNamespace

        from app.services.sync_engine import SyncEngine

        engine = SyncEngine.__new__(SyncEngine)
        engine._image_store = MagicMock()
        engine._image_http = AsyncMock()

        ref = SimpleNamespace(path="x", source_url="", images=None)

        result = await engine._upload_images(
            parsed_images=[], ref=ref, org_id="o", kb_slug="k",
        )
        assert result == []
