"""Tests for the Garage S3 image storage client (SPEC-CRAWLER-004 Fase A)."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from knowledge_ingest.s3_storage import (
    PUBLIC_IMAGE_PATH_PREFIX,
    ImageStore,
    ImageUploadResult,
)


def _make_store(**overrides: object) -> ImageStore:
    kwargs: dict[str, object] = {
        "endpoint": "garage:3900",
        "access_key": "test-access",
        "secret_key": "test-secret",
        "bucket": "klai-images",
        "region": "garage",
    }
    kwargs.update(overrides)
    return ImageStore(**kwargs)  # type: ignore[arg-type]


class TestBuildObjectKey:
    def test_content_addressed_key(self) -> None:
        data = b"fake image data"
        expected_hash = hashlib.sha256(data).hexdigest()
        key = ImageStore.build_object_key("org-123", "my-kb", data, ".png")
        assert key == f"org-123/images/my-kb/{expected_hash}.png"

    def test_normalises_extension_case(self) -> None:
        key = ImageStore.build_object_key("org-1", "kb", b"x", "PNG")
        assert key.endswith(".png")

    def test_strips_leading_dot(self) -> None:
        key = ImageStore.build_object_key("org-1", "kb", b"x", ".png")
        assert ".." not in key
        assert key.endswith(".png")


class TestBuildPublicUrl:
    def test_prefixes_with_kb_images_path(self) -> None:
        url = ImageStore.build_public_url("org/images/kb/hash.png")
        assert url == f"{PUBLIC_IMAGE_PATH_PREFIX}/org/images/kb/hash.png"


class TestValidateImage:
    def test_accepts_png(self) -> None:
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert ImageStore.validate_image(png_header) == "image/png"

    def test_accepts_jpeg(self) -> None:
        jpeg_header = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        assert ImageStore.validate_image(jpeg_header) == "image/jpeg"

    def test_accepts_gif(self) -> None:
        gif_header = b"GIF89a" + b"\x00" * 100
        assert ImageStore.validate_image(gif_header) == "image/gif"

    def test_accepts_webp(self) -> None:
        webp_header = (
            b"RIFF" + b"\x24\x00\x00\x00" + b"WEBP" + b"VP8 " + b"\x18\x00\x00\x00" + b"\x00" * 24
        )
        assert ImageStore.validate_image(webp_header) == "image/webp"

    def test_accepts_svg(self) -> None:
        svg = b"<?xml version='1.0'?><svg></svg>"
        assert ImageStore.validate_image(svg) == "image/svg+xml"

    def test_rejects_plain_text(self) -> None:
        assert ImageStore.validate_image(b"Hello, world!") is None

    def test_rejects_empty(self) -> None:
        assert ImageStore.validate_image(b"") is None


class TestUploadImage:
    @pytest.fixture()
    def store(self) -> ImageStore:
        return _make_store()

    @pytest.mark.asyncio()
    async def test_uploads_when_object_missing(self, store: ImageStore) -> None:
        from minio.error import S3Error

        mock_client = MagicMock()
        mock_client.stat_object = MagicMock(
            side_effect=S3Error("NoSuchKey", "Not found", "", "", "", "")
        )
        mock_client.put_object = MagicMock()
        store._client = mock_client  # type: ignore[attr-defined]

        result = await store.upload_image("org-1", "kb-1", b"PNG image bytes", ".png")

        assert isinstance(result, ImageUploadResult)
        assert result.deduplicated is False
        assert result.object_key.startswith("org-1/images/kb-1/")
        assert result.public_url.startswith(f"{PUBLIC_IMAGE_PATH_PREFIX}/org-1/images/kb-1/")
        mock_client.put_object.assert_called_once()

    @pytest.mark.asyncio()
    async def test_deduplicates_when_object_exists(self, store: ImageStore) -> None:
        mock_client = MagicMock()
        mock_client.stat_object = MagicMock(return_value=MagicMock())
        store._client = mock_client  # type: ignore[attr-defined]

        result = await store.upload_image("org-1", "kb-1", b"data", ".jpg")

        assert result.deduplicated is True
        mock_client.put_object.assert_not_called()
