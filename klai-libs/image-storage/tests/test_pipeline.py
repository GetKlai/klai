"""Tests for the image pipeline orchestrators (SPEC-KB-IMAGE-002).

Covers both entry points:

- :func:`download_and_upload_adapter_images` — connector adapter path
  (URLs + optional base64 parser output)
- :func:`download_and_upload_crawl_images` — web-crawl path
  (crawl4ai ``media.images`` dicts)

No real network is used — httpx.MockTransport simulates HTTP, and the
ImageStore's minio client is patched to never hit S3. EC-3 guard.
"""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock

import httpx

from klai_image_storage import (
    MAX_IMAGE_SIZE,
    ImageStore,
    download_and_upload_adapter_images,
    download_and_upload_crawl_images,
)

# A minimal valid PNG header (magic bytes + padding) that passes
# ImageStore.validate_image.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_GIF_BYTES = b"GIF89a" + b"\x00" * 64


def _mock_image_store() -> ImageStore:
    """Return an ImageStore whose minio client is a MagicMock (no S3 I/O)."""
    store = ImageStore(
        endpoint="garage:3900",
        access_key="test",
        secret_key="test",
        bucket="klai-images",
        region="garage",
    )
    from minio.error import S3Error

    mock_client = MagicMock()
    mock_client.stat_object = MagicMock(
        side_effect=S3Error("NoSuchKey", "Not found", "", "", "", "")  # pyright: ignore[reportArgumentType]
    )
    mock_client.put_object = MagicMock()
    store._client = mock_client  # type: ignore[attr-defined]
    return store


def _http_client(
    handler: httpx.MockTransport | None = None,
    responses: dict[str, httpx.Response] | None = None,
) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient backed by MockTransport.

    Either pass a custom handler, or pass a URL→Response mapping and a
    default 404 handler is generated for any URL not in the map.
    """
    if handler is None:
        mapping = responses or {}

        def _handle(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            return mapping.get(url, httpx.Response(status_code=404))

        handler = httpx.MockTransport(_handle)
    return httpx.AsyncClient(transport=handler)


# ---------------------------------------------------------------------------
# download_and_upload_adapter_images
# ---------------------------------------------------------------------------


class TestAdapterUrls:
    async def test_uploads_single_url(self) -> None:
        store = _mock_image_store()
        async with _http_client(
            responses={
                "https://example.com/img.png": httpx.Response(200, content=_PNG_BYTES),
            }
        ) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[("alt", "https://example.com/img.png")],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert len(urls) == 1
        assert urls[0].startswith("/kb-images/org-1/images/kb/")

    async def test_empty_input_returns_empty(self) -> None:
        store = _mock_image_store()
        async with _http_client() as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []

    async def test_skips_non_200_response(self) -> None:
        store = _mock_image_store()
        async with _http_client() as client:  # default 404
            urls = await download_and_upload_adapter_images(
                image_urls=[("alt", "https://example.com/missing.png")],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []

    async def test_skips_invalid_image_content(self) -> None:
        store = _mock_image_store()
        async with _http_client(
            responses={
                "https://example.com/not-an-image.png": httpx.Response(
                    200, content=b"Hello, world!"
                ),
            }
        ) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[("alt", "https://example.com/not-an-image.png")],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []

    async def test_skips_too_large_image(self) -> None:
        store = _mock_image_store()
        big = _PNG_BYTES + b"\x00" * MAX_IMAGE_SIZE  # over the limit
        async with _http_client(
            responses={
                "https://example.com/huge.png": httpx.Response(200, content=big),
            }
        ) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[("alt", "https://example.com/huge.png")],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []

    async def test_partial_failure_preserves_successes(self) -> None:
        """A 404 on one URL must not abort subsequent uploads."""
        store = _mock_image_store()
        async with _http_client(
            responses={
                "https://example.com/good.png": httpx.Response(200, content=_PNG_BYTES),
                # /bad.png defaults to 404 in the mock transport
            }
        ) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[
                    ("", "https://example.com/bad.png"),
                    ("", "https://example.com/good.png"),
                ],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert len(urls) == 1

    async def test_http_connect_error_is_skipped(self) -> None:
        """httpx.HTTPError exceptions (e.g. ConnectError) skip that URL."""
        store = _mock_image_store()

        def _raise(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        async with _http_client(handler=httpx.MockTransport(_raise)) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[("", "https://example.com/img.png")],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []


class TestAdapterParsedImages:
    async def test_uploads_base64_parsed_image(self) -> None:
        store = _mock_image_store()
        async with _http_client() as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
                parsed_images=[
                    {
                        "data_b64": base64.b64encode(_PNG_BYTES).decode(),
                        "mime_type": "image/png",
                    }
                ],
            )
        assert len(urls) == 1

    async def test_skips_too_large_parsed_image(self) -> None:
        store = _mock_image_store()
        big = _PNG_BYTES + b"\x00" * MAX_IMAGE_SIZE
        async with _http_client() as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
                parsed_images=[
                    {
                        "data_b64": base64.b64encode(big).decode(),
                        "mime_type": "image/png",
                    }
                ],
            )
        assert urls == []

    async def test_skips_invalid_parsed_image(self) -> None:
        store = _mock_image_store()
        async with _http_client() as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
                parsed_images=[
                    {
                        "data_b64": base64.b64encode(b"not an image").decode(),
                        "mime_type": "image/png",
                    }
                ],
            )
        assert urls == []

    async def test_parsed_and_url_images_combined(self) -> None:
        store = _mock_image_store()
        async with _http_client(
            responses={
                "https://example.com/a.png": httpx.Response(200, content=_PNG_BYTES),
            }
        ) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[("", "https://example.com/a.png")],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
                parsed_images=[
                    {
                        "data_b64": base64.b64encode(_GIF_BYTES).decode(),
                        "mime_type": "image/gif",
                    }
                ],
            )
        assert len(urls) == 2


# ---------------------------------------------------------------------------
# download_and_upload_crawl_images
# ---------------------------------------------------------------------------


class TestCrawlImages:
    async def test_uploads_single_valid_image(self) -> None:
        store = _mock_image_store()
        media: list[dict[str, Any]] = [{"src": "https://example.com/img.png", "alt": "x"}]
        async with _http_client(
            responses={
                "https://example.com/img.png": httpx.Response(200, content=_PNG_BYTES),
            }
        ) as client:
            urls = await download_and_upload_crawl_images(
                media_images=media,
                base_url="https://example.com/page",
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert len(urls) == 1
        assert urls[0].startswith("/kb-images/org-1/images/kb/")

    async def test_empty_media_images_returns_empty(self) -> None:
        store = _mock_image_store()
        async with _http_client() as client:
            urls = await download_and_upload_crawl_images(
                media_images=[],
                base_url="https://example.com",
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []

    async def test_filters_srcset_debris(self) -> None:
        """Cloudflare fragments like "quality=90" must be rejected before HTTP."""
        store = _mock_image_store()
        media: list[dict[str, Any]] = [
            {"src": "quality=90"},
            {"src": "fit=scale-down"},
            {"src": "w=1920"},
        ]
        async with _http_client() as client:
            urls = await download_and_upload_crawl_images(
                media_images=media,
                base_url="https://example.com",
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []

    async def test_resolves_relative_urls(self) -> None:
        store = _mock_image_store()
        media: list[dict[str, Any]] = [{"src": "/assets/img.png"}]
        async with _http_client(
            responses={
                "https://example.com/assets/img.png": httpx.Response(
                    200, content=_PNG_BYTES
                ),
            }
        ) as client:
            urls = await download_and_upload_crawl_images(
                media_images=media,
                base_url="https://example.com/page",
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert len(urls) == 1

    async def test_dedupes_identical_urls(self) -> None:
        """Two media_image entries pointing at the same URL are fetched once."""
        store = _mock_image_store()
        media: list[dict[str, Any]] = [
            {"src": "https://example.com/same.png"},
            {"src": "https://example.com/same.png"},
        ]
        call_counts: dict[str, int] = {}

        def _handle(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            call_counts[url] = call_counts.get(url, 0) + 1
            return httpx.Response(200, content=_PNG_BYTES)

        async with _http_client(handler=httpx.MockTransport(_handle)) as client:
            urls = await download_and_upload_crawl_images(
                media_images=media,
                base_url="https://example.com",
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert len(urls) == 1
        assert call_counts["https://example.com/same.png"] == 1

    async def test_falls_back_to_data_src(self) -> None:
        """When ``src`` is empty, ``data_src`` is used instead."""
        store = _mock_image_store()
        media: list[dict[str, Any]] = [
            {"src": "", "data_src": "https://example.com/lazy.png"},
        ]
        async with _http_client(
            responses={
                "https://example.com/lazy.png": httpx.Response(200, content=_PNG_BYTES),
            }
        ) as client:
            urls = await download_and_upload_crawl_images(
                media_images=media,
                base_url="https://example.com",
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert len(urls) == 1

    async def test_partial_failure_preserves_successes(self) -> None:
        store = _mock_image_store()
        media: list[dict[str, Any]] = [
            {"src": "https://example.com/bad.png"},
            {"src": "https://example.com/good.png"},
        ]
        async with _http_client(
            responses={
                "https://example.com/good.png": httpx.Response(200, content=_PNG_BYTES),
            }
        ) as client:
            urls = await download_and_upload_crawl_images(
                media_images=media,
                base_url="https://example.com",
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert len(urls) == 1

    async def test_skips_data_uri_src(self) -> None:
        store = _mock_image_store()
        media: list[dict[str, Any]] = [
            {"src": "data:image/png;base64,iVBOR..."},
        ]
        async with _http_client() as client:
            urls = await download_and_upload_crawl_images(
                media_images=media,
                base_url="https://example.com",
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []

    async def test_skips_invalid_content(self) -> None:
        store = _mock_image_store()
        media: list[dict[str, Any]] = [{"src": "https://example.com/fake.png"}]
        async with _http_client(
            responses={
                "https://example.com/fake.png": httpx.Response(200, content=b"plain"),
            }
        ) as client:
            urls = await download_and_upload_crawl_images(
                media_images=media,
                base_url="https://example.com",
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []
