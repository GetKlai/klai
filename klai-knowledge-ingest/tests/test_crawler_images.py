"""Tests for the consolidated crawl-image pipeline (SPEC-CRAWLER-004 Fase A).

Covers the acceptance criteria:
- AC-02.1 srcset debris filtered, valid URLs resolved and uploaded
- AC-02.2 content-addressed S3 key format ``{org}/images/{kb}/{sha256}.{ext}``
- AC-02.4 HTTP 4xx/5xx on one image must not abort the rest of the page
"""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from klai_image_storage import (
    ImageStore,
    ImageUploadResult,
    download_and_upload_crawl_images,
)


def _png_bytes(payload: bytes = b"one") -> bytes:
    """Minimal PNG-ish bytes that pass ``filetype.guess``."""
    return b"\x89PNG\r\n\x1a\n" + payload + b"\x00" * 100


def _make_store() -> ImageStore:
    return ImageStore(
        endpoint="garage:3900",
        access_key="ak",
        secret_key="sk",  # noqa: S106 — test fixture, not a real secret
        bucket="klai-images",
    )


def _mock_http_client(
    responses: dict[str, tuple[int, bytes]],
) -> httpx.AsyncClient:
    """Return an AsyncClient whose .get() returns the queued responses by URL."""
    client = MagicMock(spec=httpx.AsyncClient)

    async def fake_get(url: str, **_: object) -> MagicMock:
        status, content = responses.get(url, (404, b""))
        resp = MagicMock()
        resp.status_code = status
        resp.content = content
        return resp

    client.get = AsyncMock(side_effect=fake_get)
    return client  # type: ignore[return-value]


@pytest.mark.asyncio()
async def test_srcset_debris_is_filtered() -> None:
    """AC-02.1: quality=90 / fit=scale-down fragments must never hit the network."""
    store = _make_store()
    # Only the real URL should ever be fetched.
    client = _mock_http_client(
        {
            "https://help.voys.nl/real.png": (200, _png_bytes()),
        }
    )
    # Stub the S3 side effects so we can run without a real bucket.
    store._object_exists = AsyncMock(return_value=False)  # type: ignore[attr-defined]
    store.upload_image = AsyncMock(  # type: ignore[attr-defined]
        return_value=ImageUploadResult(
            object_key="org/images/support/abc.png",
            public_url="/kb-images/org/images/support/abc.png",
            deduplicated=False,
        )
    )

    result = await download_and_upload_crawl_images(
        media_images=[
            {"src": "quality=90"},
            {"src": "fit=scale-down"},
            {"src": "w=1920"},
            {"src": "https://help.voys.nl/real.png"},
        ],
        base_url="https://help.voys.nl/index",
        org_id="org",
        kb_slug="support",
        image_store=store,
        http_client=client,
    )

    assert result == ["/kb-images/org/images/support/abc.png"]
    # Confirm the filtered fragments never triggered a GET.
    called_urls = [c.args[0] for c in client.get.call_args_list]  # type: ignore[union-attr]
    assert called_urls == ["https://help.voys.nl/real.png"]


@pytest.mark.asyncio()
async def test_dedups_identical_urls_across_srcset() -> None:
    """AC-02.1: the same URL repeated in media.images must be uploaded once."""
    store = _make_store()
    client = _mock_http_client(
        {
            "https://help.voys.nl/img.png": (200, _png_bytes()),
        }
    )
    store._object_exists = AsyncMock(return_value=False)  # type: ignore[attr-defined]
    store.upload_image = AsyncMock(  # type: ignore[attr-defined]
        return_value=ImageUploadResult(
            object_key="org/images/support/img.png",
            public_url="/kb-images/org/images/support/img.png",
            deduplicated=False,
        )
    )

    result = await download_and_upload_crawl_images(
        media_images=[
            {"src": "https://help.voys.nl/img.png"},
            {"src": "https://help.voys.nl/img.png"},
            {"src": "https://help.voys.nl/img.png"},
        ],
        base_url="https://help.voys.nl/index",
        org_id="org",
        kb_slug="support",
        image_store=store,
        http_client=client,
    )

    assert result == ["/kb-images/org/images/support/img.png"]
    assert client.get.call_count == 1  # type: ignore[union-attr]


@pytest.mark.asyncio()
async def test_partial_http_failure_does_not_abort_page() -> None:
    """AC-02.4: a single 404 must not stop the other images from uploading."""
    store = _make_store()
    good_bytes = _png_bytes(b"good")
    client = _mock_http_client(
        {
            "https://help.voys.nl/broken.png": (404, b""),
            "https://help.voys.nl/ok.png": (200, good_bytes),
        }
    )

    async def fake_upload(
        org_id: str, kb_slug: str, data: bytes, ext: str
    ) -> ImageUploadResult:
        key = f"{org_id}/images/{kb_slug}/{hashlib.sha256(data).hexdigest()}.{ext}"
        return ImageUploadResult(key, f"/kb-images/{key}", deduplicated=False)

    store._object_exists = AsyncMock(return_value=False)  # type: ignore[attr-defined]
    store.upload_image = AsyncMock(side_effect=fake_upload)  # type: ignore[attr-defined]

    result = await download_and_upload_crawl_images(
        media_images=[
            {"src": "https://help.voys.nl/broken.png"},
            {"src": "https://help.voys.nl/ok.png"},
        ],
        base_url="https://help.voys.nl/index",
        org_id="org",
        kb_slug="support",
        image_store=store,
        http_client=client,
    )

    expected_hash = hashlib.sha256(good_bytes).hexdigest()
    assert result == [f"/kb-images/org/images/support/{expected_hash}.png"]


@pytest.mark.asyncio()
async def test_relative_urls_resolved_against_base() -> None:
    """AC-02.1: relative src must be resolved against the page URL before fetch."""
    store = _make_store()
    client = _mock_http_client(
        {
            "https://help.voys.nl/assets/img.png": (200, _png_bytes()),
        }
    )
    store._object_exists = AsyncMock(return_value=False)  # type: ignore[attr-defined]
    store.upload_image = AsyncMock(  # type: ignore[attr-defined]
        return_value=ImageUploadResult(
            object_key="org/images/support/x.png",
            public_url="/kb-images/org/images/support/x.png",
            deduplicated=False,
        )
    )

    result = await download_and_upload_crawl_images(
        media_images=[{"src": "/assets/img.png"}],
        base_url="https://help.voys.nl/pages/intro",
        org_id="org",
        kb_slug="support",
        image_store=store,
        http_client=client,
    )

    assert result == ["/kb-images/org/images/support/x.png"]
    called_urls = [c.args[0] for c in client.get.call_args_list]  # type: ignore[union-attr]
    assert called_urls == ["https://help.voys.nl/assets/img.png"]


@pytest.mark.asyncio()
async def test_empty_media_returns_empty_list() -> None:
    store = _make_store()
    client = MagicMock(spec=httpx.AsyncClient)
    result = await download_and_upload_crawl_images(
        media_images=[],
        base_url="https://help.voys.nl",
        org_id="org",
        kb_slug="support",
        image_store=store,
        http_client=client,
    )
    assert result == []


@pytest.mark.asyncio()
async def test_non_image_content_rejected() -> None:
    """A 200 response that doesn't pass magic-byte validation must not upload."""
    store = _make_store()
    client = _mock_http_client(
        {"https://help.voys.nl/fake.png": (200, b"Hello, this is HTML, not a PNG")}
    )
    store._object_exists = AsyncMock(return_value=False)  # type: ignore[attr-defined]
    store.upload_image = AsyncMock()  # type: ignore[attr-defined]

    result = await download_and_upload_crawl_images(
        media_images=[{"src": "https://help.voys.nl/fake.png"}],
        base_url="https://help.voys.nl/",
        org_id="org",
        kb_slug="support",
        image_store=store,
        http_client=client,
    )

    assert result == []
    store.upload_image.assert_not_called()  # type: ignore[union-attr]
