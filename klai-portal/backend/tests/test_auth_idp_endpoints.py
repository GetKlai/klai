"""SPEC-SEC-AUTH-COVERAGE-001 REQ-2 — IDP login + signup endpoints + tests.

This module covers failure-leg observability for the IDP family:
- ``idp_intent`` (REQ-2.1, 2.2) — login start
- ``idp_intent_signup`` (REQ-2.6) — signup start
- ``idp_callback`` (REQ-2.3, 2.4) — login callback (deferred to Cycle D)
- ``idp_signup_callback`` (REQ-2.7) — signup callback (deferred to Cycle G;
  158-line endpoint with multiple retry loops)

Zitadel HTTP mocked via respx; no MagicMock on ``app.api.auth.zitadel``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from auth_test_helpers import _capture_events
from fastapi import HTTPException
from structlog.testing import capture_logs

from app.api.auth import (
    IDPIntentRequest,
    IDPIntentSignupRequest,
    idp_intent,
    idp_intent_signup,
)
from app.core.config import settings


def _audit_log_patch() -> Any:
    return patch("app.api.auth.audit.log_event", AsyncMock())


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
