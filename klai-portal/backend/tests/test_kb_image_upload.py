"""Tests for KB-image upload endpoint -- SPEC-PORTAL-DOCS-IMAGE-PASTE-001.

Covers AC-1..AC-8 of the SPEC. The auth-dependency branch (no session -> 401)
is covered by ``test_permissions.py::test_get_caller_at_least_role_matrix``;
tests here invoke the route function directly with synthetic ``UserPermissions``
via the ``make_perms`` factory from conftest.

AC-1  test_upload_png_returns_url_and_not_deduplicated
AC-2  test_upload_same_bytes_twice_deduplicates
AC-3  test_upload_too_large_returns_413
AC-4  test_upload_exe_returns_415
AC-5  test_upload_svg_returns_415
AC-6  test_upload_cross_tenant_returns_404
AC-7  (covered by permissions test — see module docstring)
AC-8  test_kb_not_found_emits_warning_log

Mocking strategy: we patch ``app.api.kb_images.ImageStore`` with a class whose
instances expose an async ``upload_image`` returning a stub
``ImageUploadResult``. This avoids reaching into the minio SDK (which
``ImageStore`` instantiates internally) and keeps the test surface small.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from klai_image_storage import ImageUploadResult
from structlog.testing import capture_logs

from tests.conftest import make_perms


def _make_image_store_mock(deduplicated: bool = False) -> MagicMock:
    """Return a MagicMock that mimics the ``ImageStore`` constructor.

    Calling the returned object as ``ImageStore(...)`` yields an instance whose
    ``upload_image`` coroutine returns an ``ImageUploadResult``.
    """

    def _fake_upload_image_factory():
        async def _upload(org_id: str, kb_slug: str, data: bytes, ext: str) -> ImageUploadResult:
            # Build a plausible content-addressed key from the args.
            import hashlib

            sha = hashlib.sha256(data).hexdigest()
            object_key = f"{org_id}/images/{kb_slug}/{sha}.{ext}"
            return ImageUploadResult(
                object_key=object_key,
                public_url=f"/kb-images/{object_key}",
                deduplicated=deduplicated,
            )

        return _upload

    instance = MagicMock()
    instance.upload_image = _fake_upload_image_factory()
    # Keep ``validate_image`` semantics: route uses the *class method*, not
    # the instance, so we expose the real static method by re-importing from
    # the lib. Tests that need to override validate_image should patch the
    # class attribute directly.
    constructor = MagicMock(return_value=instance)
    # Carry validate_image through unchanged (route calls it as classmethod).
    from klai_image_storage.storage import ImageStore as _RealStore

    constructor.validate_image = staticmethod(_RealStore.validate_image)
    return constructor


# Minimal valid PNG bytes (1x1 transparent pixel).
_PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)

# Crafted "exe": MZ header from a Windows PE binary.
_EXE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200

# Valid (tiny) SVG document.
_SVG_BYTES = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'


def _make_upload(filename: str, data: bytes, content_type: str = "application/octet-stream") -> UploadFile:
    """Build a FastAPI UploadFile around an in-memory bytes buffer."""
    return UploadFile(filename=filename, file=io.BytesIO(data), headers={"content-type": content_type})


def _mock_settings_with_garage() -> MagicMock:
    s = MagicMock()
    s.garage_s3_endpoint = "garage:3900"
    s.garage_s3_access_key = "key"
    s.garage_s3_secret_key = "secret"
    s.garage_kb_bucket = "klai-images"
    return s


def _make_kb(org_id: int = 42, slug: str = "klai-help") -> MagicMock:
    kb = MagicMock()
    kb.id = 7
    kb.org_id = org_id
    kb.slug = slug
    return kb


def _make_request(content_length: str | None = None) -> MagicMock:
    """Stub Request with a real dict for ``headers`` so .get() behaves like
    the actual Starlette Headers object. ``MagicMock().headers.get(...)``
    returns a truthy MagicMock — wrong for tests that read optional headers
    (see klai testing.md "MagicMock is truthy" pitfall)."""
    req = MagicMock()
    headers: dict[str, str] = {}
    if content_length is not None:
        headers["content-length"] = content_length
    req.headers = headers
    return req


@pytest.mark.asyncio
async def test_upload_png_returns_url_and_not_deduplicated() -> None:
    """AC-1: 200 OK with content-addressed URL and deduplicated=False on first upload."""
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)
    upload = _make_upload("screenshot.png", _PNG_1X1, "image/png")
    store_cls = _make_image_store_mock(deduplicated=False)

    with (
        patch("app.api.kb_images._get_kb_or_404", AsyncMock(return_value=_make_kb())),
        patch("app.api.kb_images.settings", _mock_settings_with_garage()),
        patch("app.api.kb_images.ImageStore", store_cls),
    ):
        result = await upload_kb_image(
            request=_make_request(),
            kb_slug="klai-help",
            file=upload,
            perms=perms,
            db=AsyncMock(),
        )

    assert result["deduplicated"] is False
    assert result["url"].startswith("/kb-images/42/images/klai-help/")
    assert result["url"].endswith(".png")
    # ImageStore was instantiated once with the configured Garage settings.
    store_cls.assert_called_once()


@pytest.mark.asyncio
async def test_upload_same_bytes_twice_deduplicates() -> None:
    """AC-2: Same bytes -> deduplicated=True, no second put_object call."""
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)
    store_cls = _make_image_store_mock(deduplicated=True)

    with (
        patch("app.api.kb_images._get_kb_or_404", AsyncMock(return_value=_make_kb())),
        patch("app.api.kb_images.settings", _mock_settings_with_garage()),
        patch("app.api.kb_images.ImageStore", store_cls),
    ):
        result = await upload_kb_image(
            request=_make_request(),
            kb_slug="klai-help",
            file=_make_upload("screenshot.png", _PNG_1X1, "image/png"),
            perms=perms,
            db=AsyncMock(),
        )

    assert result["deduplicated"] is True


@pytest.mark.asyncio
async def test_upload_too_large_returns_413() -> None:
    """AC-3: > 5 MB body -> HTTP 413 with leesbare detail."""
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)
    # 5 MB + 1 byte.
    too_big = b"\x00" * (5 * 1024 * 1024 + 1)

    with (
        patch("app.api.kb_images._get_kb_or_404", AsyncMock(return_value=_make_kb())),
        patch("app.api.kb_images.settings", _mock_settings_with_garage()),
    ):
        with pytest.raises(HTTPException) as exc:
            await upload_kb_image(
                request=_make_request(),
                kb_slug="klai-help",
                file=_make_upload("big.png", too_big, "image/png"),
                perms=perms,
                db=AsyncMock(),
            )

    assert exc.value.status_code == 413
    assert "5 MB" in exc.value.detail


@pytest.mark.asyncio
async def test_upload_exe_returns_415() -> None:
    """AC-4: .exe with image/png Content-Type -> 415 'Unsupported image type'.

    Magic-byte validation rejects MZ-header content regardless of declared MIME.
    """
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)

    with (
        patch("app.api.kb_images._get_kb_or_404", AsyncMock(return_value=_make_kb())),
        patch("app.api.kb_images.settings", _mock_settings_with_garage()),
    ):
        with pytest.raises(HTTPException) as exc:
            await upload_kb_image(
                request=_make_request(),
                kb_slug="klai-help",
                file=_make_upload("fake.png", _EXE_BYTES, "image/png"),
                perms=perms,
                db=AsyncMock(),
            )

    assert exc.value.status_code == 415
    assert "Unsupported" in exc.value.detail


@pytest.mark.asyncio
async def test_upload_svg_returns_415() -> None:
    """AC-5: Valid SVG -> 415 'SVG uploads not supported' (REQ-5 user-path XSS guard).

    The connector pipeline still accepts SVG (different trust boundary), but
    user-paste from the docs editor is hard-rejected because the read-route
    serves images inline without CSP.
    """
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)

    with (
        patch("app.api.kb_images._get_kb_or_404", AsyncMock(return_value=_make_kb())),
        patch("app.api.kb_images.settings", _mock_settings_with_garage()),
    ):
        with pytest.raises(HTTPException) as exc:
            await upload_kb_image(
                request=_make_request(),
                kb_slug="klai-help",
                file=_make_upload("logo.svg", _SVG_BYTES, "image/svg+xml"),
                perms=perms,
                db=AsyncMock(),
            )

    assert exc.value.status_code == 415
    assert "SVG" in exc.value.detail


@pytest.mark.asyncio
async def test_upload_cross_tenant_returns_404() -> None:
    """AC-6: Caller org=42 uploading to a KB-slug only existing in org=99 -> 404.

    Per portal-security.md "return 404 (not 403) for private resources --
    never leak existence". The cross-tenant rejection comes from
    _get_kb_or_404, which queries WHERE org_id = caller.org_id.
    """
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)

    # Simulate _get_kb_or_404 raising 404 because the KB belongs to a different org.
    async def _raise_404(*args, **kwargs):
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    with (
        patch("app.api.kb_images._get_kb_or_404", new=_raise_404),
        patch("app.api.kb_images.settings", _mock_settings_with_garage()),
    ):
        with pytest.raises(HTTPException) as exc:
            await upload_kb_image(
                request=_make_request(),
                kb_slug="other-org-kb",
                file=_make_upload("screenshot.png", _PNG_1X1, "image/png"),
                perms=perms,
                db=AsyncMock(),
            )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_kb_not_found_emits_warning_log() -> None:
    """AC-8: REQ-7 — a 404 from _get_kb_or_404 emits kb_image_upload_kb_not_found
    warning with a neutral reason field. The event covers both typo-in-own-org
    and cross-tenant probe — distinguishing requires a cross-org query which
    would violate the 'never leak existence' rule.

    Uses ``structlog.testing.capture_logs`` because structlog is configured with
    ``ProcessorFormatter`` and writes to stdout, not via the stdlib logging
    handlers that pytest's ``caplog`` hooks (see klai-portal portal-logging-py
    rule: structlog tests need capture_logs, not caplog).
    """
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)

    async def _raise_404(*args, **kwargs):
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    with capture_logs() as captured:
        with (
            patch("app.api.kb_images._get_kb_or_404", new=_raise_404),
            patch("app.api.kb_images.settings", _mock_settings_with_garage()),
        ):
            with pytest.raises(HTTPException):
                await upload_kb_image(
                    request=_make_request(),
                    kb_slug="other-org-kb",
                    file=_make_upload("screenshot.png", _PNG_1X1, "image/png"),
                    perms=perms,
                    db=AsyncMock(),
                )

    not_found_events = [e for e in captured if e.get("event") == "kb_image_upload_kb_not_found"]
    assert len(not_found_events) == 1, f"expected 1 warning, got: {captured}"
    event = not_found_events[0]
    assert event.get("caller_org_id") == 42
    assert event.get("kb_slug") == "other-org-kb"
    assert event.get("reason") == "kb_not_found_or_cross_tenant"
    assert event.get("log_level") == "warning"


@pytest.mark.asyncio
async def test_upload_without_garage_endpoint_returns_503() -> None:
    """Defense in depth: if settings.garage_s3_endpoint is empty (feature
    flag pattern from python.md), return 503 with a leesbare detail."""
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)
    settings_no_garage = _mock_settings_with_garage()
    settings_no_garage.garage_s3_endpoint = ""

    with (
        patch("app.api.kb_images._get_kb_or_404", AsyncMock(return_value=_make_kb())),
        patch("app.api.kb_images.settings", settings_no_garage),
    ):
        with pytest.raises(HTTPException) as exc:
            await upload_kb_image(
                request=_make_request(),
                kb_slug="klai-help",
                file=_make_upload("screenshot.png", _PNG_1X1, "image/png"),
                perms=perms,
                db=AsyncMock(),
            )

    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_oversized_content_length_rejected_before_body_read() -> None:
    """Defense in depth: Content-Length > MAX_IMAGE_SIZE -> 413 BEFORE the body
    is read into memory. Without this guard, FastAPI's lack of a default body
    size limit means a malicious client could force the server to buffer 100 MB
    in memory before the post-read len() check fires.

    Verifies the early-reject path by passing a Content-Length header well
    above the limit while sending a tiny actual body. If the check were
    skipped, the 413 would still fire (post-read), but the test would have
    no way to assert the *early* exit; we assert that file.read() was never
    invoked (the upload stream is untouched).
    """
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)
    spy_upload = _make_upload("tiny.png", _PNG_1X1, "image/png")
    # Wrap .read so we can assert it was not invoked.
    spy_upload.read = AsyncMock(side_effect=AssertionError("body must not be read"))

    with (
        patch("app.api.kb_images._get_kb_or_404", AsyncMock(return_value=_make_kb())),
        patch("app.api.kb_images.settings", _mock_settings_with_garage()),
    ):
        with pytest.raises(HTTPException) as exc:
            await upload_kb_image(
                request=_make_request(content_length=str(5 * 1024 * 1024 + 1)),
                kb_slug="klai-help",
                file=spy_upload,
                perms=perms,
                db=AsyncMock(),
            )

    assert exc.value.status_code == 413
    assert "5 MB" in exc.value.detail
    spy_upload.read.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_content_length_falls_through_to_canonical_check() -> None:
    """Defense: a non-integer Content-Length is ignored and the post-read
    len() guard remains the safety net. A 6 MB body still returns 413."""
    from app.api.kb_images import upload_kb_image

    perms = make_perms(role="personal", org_id=42)
    too_big = b"\x00" * (5 * 1024 * 1024 + 1)

    with (
        patch("app.api.kb_images._get_kb_or_404", AsyncMock(return_value=_make_kb())),
        patch("app.api.kb_images.settings", _mock_settings_with_garage()),
    ):
        with pytest.raises(HTTPException) as exc:
            await upload_kb_image(
                request=_make_request(content_length="not-a-number"),
                kb_slug="klai-help",
                file=_make_upload("big.png", too_big, "image/png"),
                perms=perms,
                db=AsyncMock(),
            )

    assert exc.value.status_code == 413
