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

from typing import Any
from unittest.mock import MagicMock

import httpx

from klai_image_storage import (
    ImageStore,
    ParsedImage,
    download_and_upload_adapter_images,
    download_and_upload_crawl_images,
)

# A minimal valid PNG header (magic bytes + padding) that passes
# ImageStore.validate_image.
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
_GIF_BYTES = b"GIF89a" + b"\x00" * 64
# Matches _MAX_IMAGE_SIZE in storage.py; duplicated here so tests stay
# readable without importing a private name.
_MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024


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
        big = _PNG_BYTES + b"\x00" * _MAX_IMAGE_SIZE_BYTES  # over the limit
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
    async def test_uploads_parsed_image(self) -> None:
        store = _mock_image_store()
        async with _http_client() as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
                parsed_images=[
                    ParsedImage(data=_PNG_BYTES, ext="png", source_id="doc1:el7"),
                ],
            )
        assert len(urls) == 1

    async def test_skips_too_large_parsed_image(self) -> None:
        store = _mock_image_store()
        big = _PNG_BYTES + b"\x00" * _MAX_IMAGE_SIZE_BYTES
        async with _http_client() as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
                parsed_images=[ParsedImage(data=big, ext="png")],
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
                parsed_images=[ParsedImage(data=b"not an image", ext="png")],
            )
        assert urls == []


# ---------------------------------------------------------------------------
# SSRF regression tests — AC-15 (Notion), AC-17 (GitHub), AC-18 (Airtable)
# ---------------------------------------------------------------------------
#
# AC-16 (Confluence) matches AC-15/17 at the pipeline layer — the
# Confluence adapter emits the same ``ImageRef`` / markdown pair shape
# that feeds _download_validate_upload, so a single pipeline-layer test
# covers all four connectors for the HTTPS + docker-internal case. The
# per-adapter tests in the connector repo hit the Confluence-specific
# extraction path.


class TestAdapterSsrfGuard:
    """REQ-7.2 / AC-15 through AC-18: reject docker-internal and private-IP URLs."""

    async def test_rejects_docker_internal_hostname(self) -> None:
        """AC-15: portal-api is docker-internal — no HTTP call issued."""

        store = _mock_image_store()
        spy: list[str] = []

        def _spy(request: httpx.Request) -> httpx.Response:
            spy.append(str(request.url))
            return httpx.Response(200, content=_PNG_BYTES)

        async with _http_client(handler=httpx.MockTransport(_spy)) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[
                    (
                        "alt",
                        "https://portal-api:8010/internal/v1/orgs",
                    ),
                ],
                org_id="org-notion",
                kb_slug="kb-notion",
                image_store=store,
                http_client=client,
            )
        assert urls == []
        assert spy == []  # guard rejected; http_client.get was never called

    async def test_rejects_docker_socket_proxy(self) -> None:
        """AC-17: docker-socket-proxy (github adapter markdown image)."""

        store = _mock_image_store()
        spy: list[str] = []

        async def _spy(request: httpx.Request) -> httpx.Response:
            spy.append(str(request.url))
            return httpx.Response(200, content=_PNG_BYTES)

        async with _http_client(
            handler=httpx.MockTransport(_spy)  # type: ignore[arg-type]
        ) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[
                    (
                        "diagram",
                        "https://docker-socket-proxy:2375/v1.42/info",
                    ),
                ],
                org_id="org-gh",
                kb_slug="kb-gh",
                image_store=store,
                http_client=client,
            )
        assert urls == []
        assert spy == []

    async def test_rejects_rfc1918_literal(self) -> None:
        """AC-18: airtable attachment pointing at a private IP literal."""

        store = _mock_image_store()
        spy: list[str] = []

        def _spy(request: httpx.Request) -> httpx.Response:
            spy.append(str(request.url))
            return httpx.Response(200, content=_PNG_BYTES)

        async with _http_client(handler=httpx.MockTransport(_spy)) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[
                    ("attachment", "https://10.0.0.5/asset.png"),
                ],
                org_id="org-air",
                kb_slug="kb-air",
                image_store=store,
                http_client=client,
            )
        assert urls == []
        assert spy == []

    async def test_rejects_non_https(self) -> None:
        """HTTP (not HTTPS) is rejected by the first guard check."""

        store = _mock_image_store()
        spy: list[str] = []

        def _spy(request: httpx.Request) -> httpx.Response:
            spy.append(str(request.url))
            return httpx.Response(200, content=_PNG_BYTES)

        async with _http_client(handler=httpx.MockTransport(_spy)) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[("alt", "http://example.com/img.png")],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert urls == []
        assert spy == []

    async def test_ssrf_failure_does_not_halt_document(self) -> None:
        """AC-15: single-image SSRF rejection preserves other images."""

        store = _mock_image_store()
        async with _http_client(
            responses={
                "https://example.com/good.png": httpx.Response(
                    200, content=_PNG_BYTES
                ),
            }
        ) as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[
                    ("bad", "https://portal-api:8010/leak"),  # rejected
                    ("good", "https://example.com/good.png"),  # uploaded
                ],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
            )
        assert len(urls) == 1

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
                parsed_images=[ParsedImage(data=_GIF_BYTES, ext="gif")],
            )
        assert len(urls) == 2

    async def test_parsed_image_source_id_in_log_context(self) -> None:
        """Failing parsed images log their source_id for production triage."""
        store = _mock_image_store()
        async with _http_client() as client:
            urls = await download_and_upload_adapter_images(
                image_urls=[],
                org_id="org-1",
                kb_slug="kb",
                image_store=store,
                http_client=client,
                parsed_images=[
                    ParsedImage(
                        data=b"bogus", ext="png", source_id="report.pdf#element-42"
                    ),
                ],
            )
        assert urls == []  # invalid content is rejected; exercise source_id path


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
