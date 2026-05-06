"""Tests for KB-image auth-proxy endpoint -- SPEC-TI-009 / finding B-4.

Coverage:
- test_authenticated_user_can_read_own_org_image        -> 200
- test_authenticated_user_cannot_read_foreign_org_image -> 403
- test_unauthenticated_request_rejected                 -> 401
- test_widget_public_image_works                        -> 200
- test_partner_api_key_image_access                     -> 200
- test_404_for_nonexistent_object                       -> 404
- test_cache_control_header_set_correctly               -> private, max-age=86400
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

ORG_ID = 42
OTHER_ORG_ID = 99
KB_SLUG = "my-kb"
FILENAME = "abc123.png"


@pytest.fixture()
def kb_app():
    from fastapi import FastAPI

    from app.api.kb_images import router

    app = FastAPI()
    app.include_router(router)
    yield app
    app.dependency_overrides.clear()


def _make_session(org_id=ORG_ID):
    s = MagicMock()
    s.org_id = org_id
    return s


def _make_fake_stat(content_type="image/png"):
    stat = MagicMock()
    stat.content_type = content_type
    return stat


def _make_fake_s3_response(data=b"fake"):
    resp = MagicMock()
    resp.read = MagicMock(side_effect=[data, b""])
    resp.close = MagicMock()
    resp.release_conn = MagicMock()
    return resp


def _mock_settings():
    s = MagicMock()
    s.garage_s3_endpoint = "garage:3900"
    s.garage_s3_access_key = "key"
    s.garage_s3_secret_key = "secret"
    s.garage_kb_bucket = "klai-images"
    return s


def _mock_minio(data=b"fake", content_type="image/png"):
    mock_cls = MagicMock()
    mock_client = MagicMock()
    mock_client.stat_object.return_value = _make_fake_stat(content_type)
    mock_client.get_object.return_value = _make_fake_s3_response(data)
    mock_cls.return_value = mock_client
    return mock_cls


@pytest.mark.anyio
async def test_authenticated_user_can_read_own_org_image(kb_app):
    """AC-1: session user reads own org image -> 200."""
    from app.api.session_deps import get_optional_session

    session = _make_session(org_id=ORG_ID)
    kb_app.dependency_overrides[get_optional_session] = lambda: session
    mock_cls = _mock_minio()
    with (
        patch("app.api.kb_images.Minio", mock_cls),
        patch("app.api.kb_images.settings", _mock_settings()),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=kb_app), base_url="http://test") as client:
            resp = await client.get(f"/kb-images/{ORG_ID}/{KB_SLUG}/{FILENAME}")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, max-age=86400"


@pytest.mark.anyio
async def test_authenticated_user_cannot_read_foreign_org_image(kb_app):
    """AC-5: session user org=42 requests org=99 image -> 403."""
    from app.api.session_deps import get_optional_session

    session = _make_session(org_id=ORG_ID)
    kb_app.dependency_overrides[get_optional_session] = lambda: session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=kb_app), base_url="http://test") as client:
        resp = await client.get(f"/kb-images/{OTHER_ORG_ID}/{KB_SLUG}/{FILENAME}")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Access denied"


@pytest.mark.anyio
async def test_unauthenticated_request_rejected(kb_app):
    """No session and no Authorization header -> 401."""
    from app.api.session_deps import get_optional_session

    kb_app.dependency_overrides[get_optional_session] = lambda: None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=kb_app), base_url="http://test") as client:
        resp = await client.get(f"/kb-images/{ORG_ID}/{KB_SLUG}/{FILENAME}")
    assert resp.status_code == 401


def _mock_get_db():
    """Callable that returns an async generator yielding a MagicMock db session.

    Mirrors the signature of get_db(): called as get_db(), returns an
    async generator. The code does:
        db_gen = get_db()
        db = await db_gen.__anext__()
        ...
        await db_gen.aclose()
    """

    async def _gen():
        db = MagicMock()
        db.aclose = AsyncMock()
        yield db

    # Return _gen itself (the factory), NOT _gen() (the generator instance).
    # get_db is called as get_db() inside _resolve_caller_org_id; patching
    # app.core.database.get_db with _gen means get_db() == _gen() which is
    # correct.
    return _gen


@pytest.mark.anyio
async def test_widget_public_image_works(kb_app):
    """Widget/partner caller with matching org_id -> 200."""
    from app.api.partner_dependencies import PartnerAuthContext
    from app.api.session_deps import get_optional_session

    ctx = PartnerAuthContext(
        key_id="wgt",
        org_id=ORG_ID,
        zitadel_org_id="z",
        permissions={"chat": True},
        kb_access={},
        rate_limit_rpm=60,
    )
    kb_app.dependency_overrides[get_optional_session] = lambda: None
    mock_cls = _mock_minio(content_type="image/webp")
    with (
        patch("app.api.kb_images.get_partner_key", new=AsyncMock(return_value=ctx)),
        patch("app.core.database.get_db", new=_mock_get_db()),
        patch("app.api.kb_images.Minio", mock_cls),
        patch("app.api.kb_images.settings", _mock_settings()),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=kb_app), base_url="http://test") as client:
            resp = await client.get(
                f"/kb-images/{ORG_ID}/{KB_SLUG}/{FILENAME}",
                headers={"Authorization": "Bearer some-widget-token"},
            )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_partner_api_key_image_access(kb_app):
    """Partner API key with matching org_id -> 200."""
    from app.api.partner_dependencies import PartnerAuthContext
    from app.api.session_deps import get_optional_session

    ctx = PartnerAuthContext(
        key_id="pk_live_x",
        org_id=ORG_ID,
        zitadel_org_id="z",
        permissions={},
        kb_access={},
        rate_limit_rpm=120,
    )
    kb_app.dependency_overrides[get_optional_session] = lambda: None
    mock_cls = _mock_minio(content_type="image/jpeg")
    with (
        patch("app.api.kb_images.get_partner_key", new=AsyncMock(return_value=ctx)),
        patch("app.core.database.get_db", new=_mock_get_db()),
        patch("app.api.kb_images.Minio", mock_cls),
        patch("app.api.kb_images.settings", _mock_settings()),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=kb_app), base_url="http://test") as client:
            resp = await client.get(
                f"/kb-images/{ORG_ID}/{KB_SLUG}/{FILENAME}",
                headers={"Authorization": "Bearer pk_live_testkey"},
            )
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_404_for_nonexistent_object(kb_app):
    """Garage NoSuchKey -> 404."""
    from minio.error import S3Error

    from app.api.session_deps import get_optional_session

    session = _make_session(org_id=ORG_ID)
    kb_app.dependency_overrides[get_optional_session] = lambda: session

    mock_cls = MagicMock()
    mock_client = MagicMock()

    def _raise_no_such_key(*args, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status = 404
        mock_resp.headers = {}
        mock_resp.reason = "Not Found"
        raise S3Error(
            response=mock_resp,
            code="NoSuchKey",
            message="No such key",
            resource="/test",
            request_id="r",
            host_id="h",
        )

    # _stat_object calls stat_object first; raise NoSuchKey there so the 404
    # is returned before streaming starts (response headers not yet sent).
    mock_client.stat_object.side_effect = _raise_no_such_key
    mock_cls.return_value = mock_client

    with (
        patch("app.api.kb_images.Minio", mock_cls),
        patch("app.api.kb_images.settings", _mock_settings()),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=kb_app), base_url="http://test") as client:
            resp = await client.get(f"/kb-images/{ORG_ID}/{KB_SLUG}/{FILENAME}")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_cache_control_header_set_correctly(kb_app):
    """Successful response carries Cache-Control: private, max-age=86400."""
    from app.api.session_deps import get_optional_session

    session = _make_session(org_id=ORG_ID)
    kb_app.dependency_overrides[get_optional_session] = lambda: session
    mock_cls = _mock_minio()
    with (
        patch("app.api.kb_images.Minio", mock_cls),
        patch("app.api.kb_images.settings", _mock_settings()),
    ):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=kb_app), base_url="http://test") as client:
            resp = await client.get(f"/kb-images/{ORG_ID}/{KB_SLUG}/{FILENAME}")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "private, max-age=86400"
