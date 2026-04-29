"""SPEC-SEC-AUTH-COVERAGE-001 REQ-2 — IDP login + signup endpoints + tests.

This module covers failure-leg observability for the IDP family:
- ``idp_intent`` (REQ-2.1, 2.2) — login start
- ``idp_intent_signup`` (REQ-2.6) — signup start
- ``idp_callback`` (REQ-2.3, 2.4) — login callback failure paths
- ``idp_signup_callback`` (REQ-2.7) — signup callback failure paths

Zitadel HTTP mocked via respx where possible; ZitadelClient methods
patched directly for scenarios where the client does response-shape
validation that respx alone cannot emulate.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from auth_test_helpers import _audit_log_patch, _capture_events
from fastapi import HTTPException
from helpers import make_request
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.api.auth import (
    IDPIntentRequest,
    IDPIntentSignupRequest,
    idp_callback,
    idp_intent,
    idp_intent_signup,
    idp_signup_callback,
)
from app.core.config import settings


def _make_idp_db_mock(existing_users: list | None = None) -> AsyncMock:
    """Build an AsyncMock(spec=AsyncSession) for idp_callback / idp_signup_callback tests.

    ``existing_users`` controls the portal_users SELECT result:
      None  → no users (auto-provision branch)
      []    → no users (same as None)
      [u1]  → single org (happy path)
      [u1, u2]  → multi org (pending session branch)
    """
    db = AsyncMock(spec=AsyncSession)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    user_result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = existing_users or []
    user_result.scalars.return_value = scalars

    domain_result = MagicMock()
    domain_result.scalar_one_or_none.return_value = None

    db.execute = AsyncMock(side_effect=[user_result, domain_result])
    return db


# ---------------------------------------------------------------------------
# idp_intent_signup (REQ-2.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idp_intent_signup_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.6 — happy path emits audit + returns auth_url."""
    respx_zitadel.route().mock(
        return_value=httpx.Response(200, json={"authUrl": "https://accounts.google.com/oauth..."})
    )
    body = IDPIntentSignupRequest(
        idp_id=settings.zitadel_idp_google_id or "test-google-idp",
        locale="nl",
    )
    # Skip if no IDP configured

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        result = await idp_intent_signup(body=body)

    assert result.auth_url.startswith("https://accounts.google.com")
    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.idp.intent_signup"
    assert _capture_events(captured, "idp_intent_signup_failed") == []


@pytest.mark.asyncio
async def test_idp_intent_signup_unknown_idp(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.6 — unknown IDP id → 400 + event, Zitadel not called."""
    body = IDPIntentSignupRequest(idp_id="not-in-allowlist", locale="nl")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await idp_intent_signup(body=body)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Unknown IDP"
    audit_log.assert_not_called()
    assert respx_zitadel.calls.call_count == 0
    events = _capture_events(captured, "idp_intent_signup_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "unknown_idp"
    assert events[0]["outcome"] == "400"


@pytest.mark.asyncio
async def test_idp_intent_signup_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.6 — Zitadel 5xx → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))
    body = IDPIntentSignupRequest(idp_id=settings.zitadel_idp_google_id, locale="nl")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await idp_intent_signup(body=body)

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_intent_signup_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502


# ---------------------------------------------------------------------------
# idp_intent (REQ-2.1, 2.2) — login start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idp_intent_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.1 — happy path emits audit + returns auth_url."""
    respx_zitadel.route().mock(
        return_value=httpx.Response(200, json={"authUrl": "https://accounts.google.com/oauth..."})
    )
    body = IDPIntentRequest(idp_id=settings.zitadel_idp_google_id, auth_request_id="ar-1")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        result = await idp_intent(body=body)

    assert result.auth_url.startswith("https://accounts.google.com")
    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.idp.intent"
    assert _capture_events(captured, "idp_intent_failed") == []


@pytest.mark.asyncio
async def test_idp_intent_unknown_idp(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.2 — unknown IDP id → 400 + event, Zitadel not called."""
    body = IDPIntentRequest(idp_id="not-in-allowlist", auth_request_id="ar-2")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await idp_intent(body=body)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Unknown IDP"
    audit_log.assert_not_called()
    assert respx_zitadel.calls.call_count == 0
    events = _capture_events(captured, "idp_intent_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "unknown_idp"
    assert events[0]["outcome"] == "400"


@pytest.mark.asyncio
async def test_idp_intent_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.2 — Zitadel 5xx → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))
    body = IDPIntentRequest(idp_id=settings.zitadel_idp_google_id, auth_request_id="ar-3")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await idp_intent(body=body)

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_intent_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502


@pytest.mark.asyncio
async def test_idp_intent_missing_auth_url(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.2 — Zitadel returns 200 with no authUrl → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))
    body = IDPIntentRequest(idp_id=settings.zitadel_idp_google_id, auth_request_id="ar-4")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await idp_intent(body=body)

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_intent_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "missing_auth_url"


@pytest.mark.asyncio
async def test_idp_intent_signup_missing_auth_url(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.6 — Zitadel returns 200 but no authUrl → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))
    body = IDPIntentSignupRequest(idp_id=settings.zitadel_idp_google_id, locale="nl")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await idp_intent_signup(body=body)

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_intent_signup_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "missing_auth_url"


# ---------------------------------------------------------------------------
# idp_callback (REQ-2.3, 2.4) — login callback failure-leg coverage
#
# Note: existing tests/test_idp_callback_provision.py covers the auto-provision
# happy path. This module adds failure-leg observability tests per REQ-2.4.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idp_callback_session_creation_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.4 — create_session_with_idp_intent 5xx → 302 to failure_url + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))
    db = _make_idp_db_mock()

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        result = await idp_callback(id="intent-1", token="tok-1", auth_request_id="ar-1", db=db)

    assert result.status_code == 302
    assert "/login?authRequest=ar-1" in result.headers["location"]
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_callback_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "session_creation_5xx"
    assert events[0]["zitadel_status"] == 502


@pytest.mark.asyncio
async def test_idp_callback_no_session_in_response(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.4 — Zitadel returns no sessionId/sessionToken → 302 + missing_session event.

    We patch ``create_session_with_idp_intent`` directly here because the
    "session dict has no keys" shape is impossible to produce via respx —
    the ZitadelClient method does additional validation on the intent
    before returning. Patching the method targets the exact contract:
    when create returns an empty/partial dict, idp_callback's
    ``missing_session`` branch fires.
    """
    db = _make_idp_db_mock()

    with (
        capture_logs() as captured,
        _audit_log_patch() as audit_log,
        patch(
            "app.api.auth.zitadel.create_session_with_idp_intent",
            AsyncMock(return_value={}),  # no sessionId / sessionToken
        ),
    ):
        result = await idp_callback(id="intent-2", token="tok-2", auth_request_id="ar-2", db=db)

    assert result.status_code == 302
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_callback_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "missing_session"


@pytest.mark.asyncio
async def test_idp_callback_finalize_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.4 — finalize_auth_request 5xx → 302 to failure_url + event.

    Setup: patch ``create_session_with_idp_intent`` + ``get_session_details``
    to return valid shapes (real Zitadel response shapes are validated
    inside the client), then mount respx for finalize_auth_request 5xx.
    """
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(502, json={"error": "bad gw"})
    )

    db = _make_idp_db_mock()

    with (
        capture_logs() as captured,
        _audit_log_patch() as audit_log,
        patch(
            "app.api.auth.zitadel.create_session_with_idp_intent",
            AsyncMock(return_value={"sessionId": "sess-1", "sessionToken": "tok-x"}),
        ),
        patch(
            "app.api.auth.zitadel.get_session_details",
            AsyncMock(return_value={"zitadel_user_id": "uid-x", "email": "x@example.com"}),
        ),
    ):
        result = await idp_callback(id="intent-3", token="tok-3", auth_request_id="ar-3", db=db)

    assert result.status_code == 302
    assert "/login?authRequest=ar-3" in result.headers["location"]
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_callback_failed")
    finalize_events = [e for e in events if e["reason"] == "finalize_5xx"]
    assert len(finalize_events) == 1
    assert finalize_events[0]["zitadel_status"] == 502


# ---------------------------------------------------------------------------
# idp_signup_callback (REQ-2.7) — signup callback failure-leg coverage
#
# 158-line endpoint with multiple retry loops and branches. Tests cover the
# 3 most-common failure exits via direct ZitadelClient method patches.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idp_signup_callback_retrieve_intent_5xx() -> None:
    """REQ-2.7 — retrieve_idp_intent 5xx → 302 + retrieve_intent_5xx event."""
    db = _make_idp_db_mock()
    error = httpx.HTTPStatusError(
        "bad gw",
        request=httpx.Request("POST", "https://x"),
        response=httpx.Response(502, json={"error": "bad gw"}),
    )

    with (
        capture_logs() as captured,
        _audit_log_patch() as audit_log,
        patch(
            "app.api.auth.zitadel.retrieve_idp_intent",
            AsyncMock(side_effect=error),
        ),
    ):
        result = await idp_signup_callback(id="intent-1", token="tok-1", request=make_request(), locale="nl", db=db)

    assert result.status_code == 302
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_signup_callback_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "retrieve_intent_5xx"
    assert events[0]["zitadel_status"] == 502


@pytest.mark.asyncio
async def test_idp_signup_callback_create_session_5xx() -> None:
    """REQ-2.7 — create_session_for_user_idp 5xx (non-404) → 302 + create_session_5xx event."""
    db = _make_idp_db_mock()
    error = httpx.HTTPStatusError(
        "bad gw",
        request=httpx.Request("POST", "https://x"),
        response=httpx.Response(502, json={"error": "bad gw"}),
    )

    with (
        capture_logs() as captured,
        _audit_log_patch() as audit_log,
        patch(
            "app.api.auth.zitadel.retrieve_idp_intent",
            AsyncMock(return_value={"userId": "uid-x"}),
        ),
        patch(
            "app.api.auth.zitadel.create_session_for_user_idp",
            AsyncMock(side_effect=error),
        ),
    ):
        result = await idp_signup_callback(id="intent-2", token="tok-2", request=make_request(), locale="nl", db=db)

    assert result.status_code == 302
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_signup_callback_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "create_session_5xx"
    assert events[0]["zitadel_status"] == 502


@pytest.mark.asyncio
async def test_idp_signup_callback_get_session_5xx() -> None:
    """REQ-2.7 — get_session 5xx after successful create_session → 302 + get_session_5xx event."""
    db = _make_idp_db_mock()
    error = httpx.HTTPStatusError(
        "bad gw",
        request=httpx.Request("POST", "https://x"),
        response=httpx.Response(502, json={"error": "bad gw"}),
    )

    with (
        capture_logs() as captured,
        _audit_log_patch() as audit_log,
        patch(
            "app.api.auth.zitadel.retrieve_idp_intent",
            AsyncMock(return_value={"userId": "uid-x"}),
        ),
        patch(
            "app.api.auth.zitadel.create_session_for_user_idp",
            AsyncMock(return_value={"sessionId": "sess-1", "sessionToken": "tok-x"}),
        ),
        patch(
            "app.api.auth.zitadel.get_session",
            AsyncMock(side_effect=error),
        ),
    ):
        result = await idp_signup_callback(id="intent-3", token="tok-3", request=make_request(), locale="nl", db=db)

    assert result.status_code == 302
    audit_log.assert_not_called()
    events = _capture_events(captured, "idp_signup_callback_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "get_session_5xx"
    assert events[0]["zitadel_status"] == 502
