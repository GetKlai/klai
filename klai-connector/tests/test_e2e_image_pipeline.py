"""End-to-end integration tests for the image storage pipeline.

Tests the full flow: adapter -> parser -> image extraction -> S3 upload -> ingest client,
using mocks for external services (S3, HTTP, knowledge-ingest).
"""

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.adapters.base import DocumentRef, ImageRef
from app.adapters.notion import _extract_image_blocks
from app.clients.knowledge_ingest import _build_payload
from app.services.image_utils import extract_markdown_image_urls, resolve_relative_url
from app.services.s3_storage import ImageStore, ImageUploadResult
from app.services.sync_images import download_and_upload_images

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _png_bytes() -> bytes:
    """Minimal PNG-like bytes that pass filetype validation."""
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def _mock_image_store() -> MagicMock:
    """Create a mock ImageStore that accepts all images."""
    store = MagicMock(spec=ImageStore)
    store.validate_image = MagicMock(return_value="image/png")

    async def _upload(*args, **kwargs):
        return ImageUploadResult(
            object_key=f"org/images/kb/{args[3]}", presigned_url="https://garage/signed-url", deduplicated=False,
        )

    store.upload_image = _upload
    return store


def _mock_http_ok(content: bytes = b"") -> AsyncMock:
    """Create a mock httpx client that returns 200 with given content."""
    client = AsyncMock()
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content or _png_bytes()
    client.get = AsyncMock(return_value=resp)
    return client


# ---------------------------------------------------------------------------
# E2E: GitHub Markdown → Image URLs → S3 Upload → Ingest
# ---------------------------------------------------------------------------

class TestGitHubMarkdownE2E:
    """Full pipeline for a GitHub markdown document with embedded images."""

    @pytest.mark.asyncio
    async def test_github_markdown_with_images(self):
        """GitHub markdown with relative image paths → resolved → downloaded → uploaded → ingested."""
        # 1. Simulate adapter output
        markdown = """# Architecture

![overview](docs/images/arch-overview.png)

Some explanation text here.

![detail](docs/images/detail.jpg)
"""
        # 2. Extract image URLs
        raw_urls = extract_markdown_image_urls(markdown)
        assert len(raw_urls) == 2
        assert raw_urls[0] == ("overview", "docs/images/arch-overview.png")
        assert raw_urls[1] == ("detail", "docs/images/detail.jpg")

        # 3. Resolve relative URLs against GitHub raw content
        base = "https://raw.githubusercontent.com/acme/repo/main/"
        resolved = [(alt, resolve_relative_url(url, base)) for alt, url in raw_urls]
        assert resolved[0][1] == "https://raw.githubusercontent.com/acme/repo/main/docs/images/arch-overview.png"
        assert resolved[1][1] == "https://raw.githubusercontent.com/acme/repo/main/docs/images/detail.jpg"

        # 4. Download + upload
        store = _mock_image_store()
        http = _mock_http_ok()

        presigned_urls = await download_and_upload_images(
            image_urls=resolved,
            org_id="org-acme",
            kb_slug="docs",
            image_store=store,
            http_client=http,
        )
        assert len(presigned_urls) == 2
        assert all(url.startswith("https://garage/") for url in presigned_urls)

        # 5. Build ingest payload with image_urls
        payload = _build_payload(
            org_id="org-acme",
            kb_slug="docs",
            path="docs/architecture.md",
            content=markdown,
            source_connector_id="conn-123",
            source_ref="acme/repo:main:docs/architecture.md",
            source_url="",
            content_type="kb_article",
            image_urls=presigned_urls,
        )
        assert payload["extra"]["image_urls"] == presigned_urls
        assert payload["content"] == markdown


# ---------------------------------------------------------------------------
# E2E: Web Crawler → Image URLs → S3 Upload → Ingest
# ---------------------------------------------------------------------------

class TestWebCrawlerE2E:
    """Full pipeline for a web crawler page with images."""

    @pytest.mark.asyncio
    async def test_webcrawler_page_with_images(self):
        """Web crawler markdown with absolute and relative images."""
        page_url = "https://docs.example.com/guide/setup"
        markdown = """# Setup Guide

![step1](https://cdn.example.com/images/step1.png)

Follow these steps:

![step2](images/step2.webp)

![step3](/assets/step3.gif)
"""
        raw_urls = extract_markdown_image_urls(markdown)
        assert len(raw_urls) == 3

        # Resolve relative URLs
        resolved = [(alt, resolve_relative_url(url, page_url)) for alt, url in raw_urls]
        assert resolved[0][1] == "https://cdn.example.com/images/step1.png"  # absolute, unchanged
        assert resolved[1][1] == "https://docs.example.com/guide/images/step2.webp"  # relative
        assert resolved[2][1] == "https://docs.example.com/assets/step3.gif"  # root-relative

        # Download + upload
        presigned_urls = await download_and_upload_images(
            image_urls=resolved,
            org_id="org-ex",
            kb_slug="kb",
            image_store=_mock_image_store(),
            http_client=_mock_http_ok(),
        )
        assert len(presigned_urls) == 3


# ---------------------------------------------------------------------------
# E2E: Notion → Image Blocks → S3 Upload → Ingest
# ---------------------------------------------------------------------------

class TestNotionE2E:
    """Full pipeline for Notion pages with image blocks."""

    def test_notion_image_block_extraction(self):
        """Notion image blocks (external + file) are extracted to ImageRef."""
        blocks = [
            {
                "type": "paragraph",
                "paragraph": {"rich_text": [{"plain_text": "Hello"}]},
            },
            {
                "type": "image",
                "id": "img-block-1",
                "image": {
                    "type": "external",
                    "external": {"url": "https://example.com/diagram.png"},
                    "caption": [{"plain_text": "Architecture diagram"}],
                },
            },
            {
                "type": "image",
                "id": "img-block-2",
                "image": {
                    "type": "file",
                    "file": {"url": "https://prod-files.notion.so/signed/img.jpg?token=abc"},
                    "caption": [],
                },
            },
            {
                "type": "toggle",
                "_children": [
                    {
                        "type": "image",
                        "id": "nested-img",
                        "image": {
                            "type": "external",
                            "external": {"url": "https://example.com/nested.png"},
                            "caption": [{"plain_text": "Nested image"}],
                        },
                    }
                ],
            },
        ]

        images = _extract_image_blocks(blocks)

        assert len(images) == 3
        assert images[0].url == "https://example.com/diagram.png"
        assert images[0].alt == "Architecture diagram"
        assert images[1].url == "https://prod-files.notion.so/signed/img.jpg?token=abc"
        assert images[1].alt == ""
        assert images[2].url == "https://example.com/nested.png"
        assert images[2].alt == "Nested image"

    @pytest.mark.asyncio
    async def test_notion_images_uploaded(self):
        """Extracted Notion image URLs are downloaded and uploaded."""
        image_urls = [
            ("diagram", "https://example.com/diagram.png"),
            ("photo", "https://prod-files.notion.so/signed/photo.jpg?token=xyz"),
        ]

        presigned_urls = await download_and_upload_images(
            image_urls=image_urls,
            org_id="org-notion",
            kb_slug="notion-kb",
            image_store=_mock_image_store(),
            http_client=_mock_http_ok(),
        )
        assert len(presigned_urls) == 2


# ---------------------------------------------------------------------------
# E2E: PDF with Unstructured Image Elements → S3 Upload → Ingest
# ---------------------------------------------------------------------------

class TestPDFImageE2E:
    """Full pipeline for PDF documents with embedded images."""

    @pytest.mark.asyncio
    async def test_pdf_base64_images_uploaded(self):
        """Base64 images from Unstructured PDF partition are uploaded to S3."""
        png_data = _png_bytes()
        b64_data = base64.b64encode(png_data).decode()

        parsed_images = [
            {"data_b64": b64_data, "mime_type": "image/png"},
            {"data_b64": b64_data, "mime_type": "image/jpeg"},
        ]

        presigned_urls = await download_and_upload_images(
            image_urls=[],  # No markdown images in PDF
            org_id="org-pdf",
            kb_slug="reports",
            image_store=_mock_image_store(),
            http_client=_mock_http_ok(),
            parsed_images=parsed_images,
        )
        assert len(presigned_urls) == 2


# ---------------------------------------------------------------------------
# E2E: Mixed scenario — markdown + parsed images
# ---------------------------------------------------------------------------

class TestMixedE2E:
    """Combined markdown images + parsed images from a single document."""

    @pytest.mark.asyncio
    async def test_mixed_markdown_and_parsed_images(self):
        """Both markdown image URLs and parser-extracted images are uploaded."""
        png_data = _png_bytes()

        presigned_urls = await download_and_upload_images(
            image_urls=[("logo", "https://example.com/logo.png")],
            org_id="org-mix",
            kb_slug="mixed",
            image_store=_mock_image_store(),
            http_client=_mock_http_ok(),
            parsed_images=[{"data_b64": base64.b64encode(png_data).decode(), "mime_type": "image/png"}],
        )
        # 1 parsed + 1 markdown = 2 total
        assert len(presigned_urls) == 2


# ---------------------------------------------------------------------------
# E2E: Resilience — partial failures
# ---------------------------------------------------------------------------

class TestResilienceE2E:
    """Pipeline resilience: partial failures don't block other images."""

    @pytest.mark.asyncio
    async def test_one_download_fails_others_succeed(self):
        """When one image download fails, the rest still get uploaded."""
        store = _mock_image_store()
        http = AsyncMock()

        call_count = 0

        async def mixed_get(url):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ConnectionError("Network timeout")
            resp = MagicMock()
            resp.status_code = 200
            resp.content = _png_bytes()
            return resp

        http.get = mixed_get

        presigned_urls = await download_and_upload_images(
            image_urls=[("a", "https://ok.com/1.png"), ("b", "https://fail.com/2.png"), ("c", "https://ok.com/3.png")],
            org_id="org-r",
            kb_slug="kb",
            image_store=store,
            http_client=http,
        )
        # 2 out of 3 should succeed
        assert len(presigned_urls) == 2

    @pytest.mark.asyncio
    async def test_non_200_response_skipped(self):
        """HTTP 404 responses are skipped gracefully."""
        store = _mock_image_store()
        http = AsyncMock()
        resp = MagicMock()
        resp.status_code = 404
        resp.content = b""
        http.get = AsyncMock(return_value=resp)

        presigned_urls = await download_and_upload_images(
            image_urls=[("missing", "https://example.com/gone.png")],
            org_id="org-r",
            kb_slug="kb",
            image_store=store,
            http_client=http,
        )
        assert presigned_urls == []


# ---------------------------------------------------------------------------
# E2E: Ingest payload integrity
# ---------------------------------------------------------------------------

class TestIngestPayloadE2E:
    """Verify the ingest payload correctly carries image_urls through."""

    def test_payload_with_images_and_source_url(self):
        """image_urls and source_url both flow through the extra dict."""
        payload = _build_payload(
            org_id="org-1",
            kb_slug="kb-1",
            path="guide.md",
            content="# Guide\n![img](img.png)",
            source_connector_id="conn-1",
            source_ref="acme/repo:main:guide.md",
            source_url="https://github.com/acme/repo/blob/main/guide.md",
            content_type="kb_article",
            image_urls=["https://garage/org-1/img1.png", "https://garage/org-1/img2.jpg"],
        )

        assert payload["org_id"] == "org-1"
        assert payload["extra"]["source_url"] == "https://github.com/acme/repo/blob/main/guide.md"
        assert payload["extra"]["image_urls"] == ["https://garage/org-1/img1.png", "https://garage/org-1/img2.jpg"]
        assert "content" in payload

    def test_payload_without_images(self):
        """When no images, extra should not contain image_urls."""
        payload = _build_payload(
            org_id="org-1",
            kb_slug="kb-1",
            path="notes.txt",
            content="Plain text",
            source_connector_id="conn-1",
            source_ref="ref",
            content_type="kb_article",
            image_urls=None,
        )

        assert "image_urls" not in payload.get("extra", {})

    def test_document_ref_carries_images(self):
        """DocumentRef.images field works as metadata carrier."""
        images = [
            ImageRef(url="https://garage/signed/img1.png", alt="diagram", source_path="images/diagram.png"),
            ImageRef(url="https://garage/signed/img2.jpg", alt="", source_path="images/photo.jpg"),
        ]
        ref = DocumentRef(
            path="architecture.md",
            ref="abc123",
            size=5000,
            content_type="kb_article",
            source_ref="acme/repo:main:architecture.md",
            images=images,
        )

        assert ref.images is not None
        assert len(ref.images) == 2
        assert ref.images[0].alt == "diagram"
        assert ref.images[1].source_path == "images/photo.jpg"
