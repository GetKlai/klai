"""Tests for the Garage S3 image storage client (SPEC-KB-IMAGE-002).

Union of the tests that previously lived in
``klai-connector/tests/test_s3_storage.py`` and
``klai-knowledge-ingest/tests/test_s3_storage.py``; the two suites were
≈98% identical.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from klai_image_storage import (
    ImageStore,
    ImageUploadResult,
)


class TestBuildObjectKey:
    def test_content_addressed_key(self) -> None:
        """Object key uses SHA256 hash for deduplication."""
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

    def test_key_format_has_three_segments(self) -> None:
        """Key format is {org}/images/{kb}/{sha256}.{ext} — four slash-separated parts."""
        key = ImageStore.build_object_key("org-42", "support", b"PNG\x00example", "png")
        assert key.startswith("org-42/images/support/")
        assert key.endswith(".png")

    def test_key_is_deterministic(self) -> None:
        """Same bytes + metadata → same key (content-addressed contract)."""
        k1 = ImageStore.build_object_key("org-1", "kb", b"same bytes", "png")
        k2 = ImageStore.build_object_key("org-1", "kb", b"same bytes", "png")
        assert k1 == k2

    def test_different_bytes_different_key(self) -> None:
        k1 = ImageStore.build_object_key("org-1", "kb", b"bytes-a", "png")
        k2 = ImageStore.build_object_key("org-1", "kb", b"bytes-b", "png")
        assert k1 != k2


class TestBuildPublicUrl:
    def test_prefixes_with_kb_images_path(self) -> None:
        # `/kb-images/` is an intentional wire-level invariant —
        # asserted as a literal so no refactor can silently change it.
        url = ImageStore.build_public_url("org/images/kb/hash.png")
        assert url == "/kb-images/org/images/kb/hash.png"

    def test_public_url_format(self) -> None:
        """Public URL uses /kb-images/ prefix."""
        url = ImageStore.build_public_url("org-1/images/kb/abc123.png")
        assert url == "/kb-images/org-1/images/kb/abc123.png"


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
            b"RIFF"
            + b"\x24\x00\x00\x00"
            + b"WEBP"
            + b"VP8 "
            + b"\x18\x00\x00\x00"
            + b"\x00" * 24
        )
        assert ImageStore.validate_image(webp_header) == "image/webp"

    def test_accepts_svg(self) -> None:
        svg = b"<?xml version='1.0'?><svg></svg>"
        assert ImageStore.validate_image(svg) == "image/svg+xml"

    def test_accepts_svg_without_xml_declaration(self) -> None:
        svg = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
        assert ImageStore.validate_image(svg) == "image/svg+xml"

    def test_rejects_plain_text(self) -> None:
        assert ImageStore.validate_image(b"Hello, world!") is None

    def test_rejects_empty(self) -> None:
        assert ImageStore.validate_image(b"") is None


class TestUploadImage:
    async def test_uploads_when_object_missing(self, fresh_store: ImageStore) -> None:
        """upload_image puts the object and returns a public URL."""
        from minio.error import S3Error

        mock_client = MagicMock()
        mock_client.stat_object = MagicMock(
            side_effect=S3Error("NoSuchKey", "Not found", "", "", "", "")  # pyright: ignore[reportArgumentType]
        )
        mock_client.put_object = MagicMock()
        fresh_store._client = mock_client  # type: ignore[attr-defined]

        result = await fresh_store.upload_image("org-1", "kb-1", b"PNG image bytes", ".png")

        assert isinstance(result, ImageUploadResult)
        assert result.deduplicated is False
        assert result.object_key.startswith("org-1/images/kb-1/")
        assert result.public_url.startswith("/kb-images/org-1/images/kb-1/")
        mock_client.put_object.assert_called_once()

    async def test_deduplicates_when_object_exists(self, fresh_store: ImageStore) -> None:
        """upload_image skips upload when object already exists."""
        mock_client = MagicMock()
        mock_client.stat_object = MagicMock(return_value=MagicMock())
        mock_client.put_object = MagicMock()
        fresh_store._client = mock_client  # type: ignore[attr-defined]

        result = await fresh_store.upload_image("org-1", "kb-1", b"data", ".jpg")

        assert result.public_url.startswith("/kb-images/")
        assert result.deduplicated is True
        mock_client.put_object.assert_not_called()

    async def test_handles_missing_object_then_uploads(self, fresh_store: ImageStore) -> None:
        """stat_object raising S3Error triggers upload path."""
        from minio.error import S3Error

        mock_client = MagicMock()
        mock_client.stat_object = MagicMock(
            side_effect=S3Error("NoSuchKey", "Not found", "", "", "", "")  # pyright: ignore[reportArgumentType]
        )
        mock_client.put_object = MagicMock()
        fresh_store._client = mock_client  # type: ignore[attr-defined]

        result = await fresh_store.upload_image("org-1", "kb-1", b"new data", ".png")

        assert result.deduplicated is False
        mock_client.put_object.assert_called_once()


class TestImageUploadResult:
    """Result dataclass is frozen and captures all three fields."""

    def test_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        r = ImageUploadResult(object_key="k", public_url="/kb-images/k", deduplicated=False)
        with pytest.raises(FrozenInstanceError):
            r.object_key = "other"  # type: ignore[misc]

    def test_fields(self) -> None:
        r = ImageUploadResult(object_key="k", public_url="/kb-images/k", deduplicated=True)
        assert r.object_key == "k"
        assert r.public_url == "/kb-images/k"
        assert r.deduplicated is True
