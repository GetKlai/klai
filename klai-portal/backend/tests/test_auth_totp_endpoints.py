"""SPEC-SEC-AUTH-COVERAGE-001 REQ-1 — TOTP setup / confirm / login + tests.

Failure-leg + happy-path coverage for the three TOTP endpoints in auth.py:
- ``POST /api/auth/totp/setup``
- ``POST /api/auth/totp/confirm``
- ``POST /api/auth/totp-login``

Every failure leg emits a ``*_failed`` structured event; success on
setup/confirm emits ``audit.log_event``. ``totp_login`` keeps its
existing ``auth.totp.failed`` audit log on invalid_code (REQ-1.6) AND
adds a ``totp_login_failed`` structured event for ops alerting.

Zitadel HTTP mocked via respx against the real ``ZitadelClient`` (REQ-5.7).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from auth_test_helpers import _audit_log_patch, _capture_events
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.api.auth import (
    _TOTP_MAX_FAILURES,
    TOTPConfirmRequest,
    TOTPLoginRequest,
    _pending_totp,
    totp_confirm,
    totp_login,
    totp_setup,
)


def _put_pending(failures: int = 0) -> str:
    """Insert a fresh pending TOTP entry and return the temp_token."""
    return _pending_totp.put({"session_id": "sess-1", "session_token": "tok-1", "failures": failures})


# ---------------------------------------------------------------------------
# totp_setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_setup_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.1 — setup happy path emits audit + returns uri/secret."""
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={"uri": "otpauth://totp/x", "totpSecret": "ABCD"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        result = await totp_setup(user_id="uid-1")

    assert result.uri == "otpauth://totp/x"
    assert result.secret == "ABCD"
    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.totp.setup"
    assert audit_log.call_args.kwargs["actor"] == "uid-1"
    assert _capture_events(captured, "totp_setup_failed") == []


@pytest.mark.asyncio
async def test_totp_setup_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.2 — setup 5xx → 502 + event + no audit."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await totp_setup(user_id="uid-1")

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "totp_setup_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502
    assert events[0]["actor_user_id"] == "uid-1"
    assert events[0]["outcome"] == "502"
    assert events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# totp_confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_confirm_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.3 — confirm happy path emits audit."""
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await totp_confirm(body=TOTPConfirmRequest(code="123456"), user_id="uid-1")

    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.totp.confirmed"
    assert _capture_events(captured, "totp_confirm_failed") == []


@pytest.mark.asyncio
async def test_totp_confirm_invalid_code(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.4 — confirm 4xx → 400 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(400, json={"error": "bad"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await totp_confirm(body=TOTPConfirmRequest(code="000000"), user_id="uid-1")

    assert exc.value.status_code == 400
    audit_log.assert_not_called()
    events = _capture_events(captured, "totp_confirm_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "invalid_code"
    assert events[0]["zitadel_status"] == 400
    assert events[0]["outcome"] == "400"
    assert events[0]["log_level"] == "warning"


@pytest.mark.asyncio
async def test_totp_confirm_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.5 — confirm 5xx → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await totp_confirm(body=TOTPConfirmRequest(code="123456"), user_id="uid-1")

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "totp_confirm_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502
    assert events[0]["outcome"] == "502"
    assert events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# totp_login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_totp_login_expired_token(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.8 — unknown temp_token → 400 + event with reason=expired_token."""
    body = TOTPLoginRequest(temp_token="never-existed", code="123456", auth_request_id="ar-1")
    db = AsyncMock(spec=AsyncSession)
    response = MagicMock()

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await totp_login(body=body, response=response, db=db)

    assert exc.value.status_code == 400
    assert "Session expired" in exc.value.detail
    audit_log.assert_not_called()
    events = _capture_events(captured, "totp_login_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "expired_token"
    assert events[0]["outcome"] == "400"
    assert events[0]["log_level"] == "warning"


@pytest.mark.asyncio
async def test_totp_login_already_locked(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.7 — already-locked token → 429 + event + token removed."""
    temp_token = _put_pending(failures=_TOTP_MAX_FAILURES)
    body = TOTPLoginRequest(temp_token=temp_token, code="123456", auth_request_id="ar-2")
    db = AsyncMock(spec=AsyncSession)
    response = MagicMock()

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await totp_login(body=body, response=response, db=db)

    assert exc.value.status_code == 429
    audit_log.assert_not_called()  # immediate lockout — no audit fires
    events = _capture_events(captured, "totp_login_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "lockout"
    assert events[0]["failures"] == _TOTP_MAX_FAILURES
    assert events[0]["outcome"] == "429"
    assert events[0]["log_level"] == "error"
    # Zitadel was NOT called — short-circuit
    assert respx_zitadel.calls.call_count == 0
    # Token cleaned up
    assert _pending_totp.get(temp_token) is None


@pytest.mark.asyncio
async def test_totp_login_invalid_code_first_failure(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.6 — wrong code (1st failure) → 400 + emit + audit, failures=1."""
    respx_zitadel.route().mock(return_value=httpx.Response(401, json={"error": "bad code"}))
    temp_token = _put_pending(failures=0)
    body = TOTPLoginRequest(temp_token=temp_token, code="000000", auth_request_id="ar-3")
    db = AsyncMock(spec=AsyncSession)
    response = MagicMock()

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await totp_login(body=body, response=response, db=db)

    assert exc.value.status_code == 400
    audit_log.assert_called_once()  # auth.totp.failed audit fires for invalid_code
    assert audit_log.call_args.kwargs["action"] == "auth.totp.failed"
    events = _capture_events(captured, "totp_login_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "invalid_code"
    assert events[0]["failures"] == 1
    assert events[0]["zitadel_status"] == 401
    assert events[0]["outcome"] == "400"
    # Token survives for retry
    assert _pending_totp.get(temp_token) is not None
    _pending_totp.pop(temp_token)  # cleanup


@pytest.mark.asyncio
async def test_totp_login_invalid_code_lockout(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.7 — wrong code that pushes failures to MAX → 429 + lockout event."""
    respx_zitadel.route().mock(return_value=httpx.Response(401, json={"error": "bad code"}))
    temp_token = _put_pending(failures=_TOTP_MAX_FAILURES - 1)
    body = TOTPLoginRequest(temp_token=temp_token, code="000000", auth_request_id="ar-4")
    db = AsyncMock(spec=AsyncSession)
    response = MagicMock()

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await totp_login(body=body, response=response, db=db)

    assert exc.value.status_code == 429
    audit_log.assert_called_once()  # auth.totp.failed audit still fires before the lockout check
    events = _capture_events(captured, "totp_login_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "lockout"
    assert events[0]["failures"] == _TOTP_MAX_FAILURES
    assert events[0]["outcome"] == "429"
    # Token removed at lockout
    assert _pending_totp.get(temp_token) is None


@pytest.mark.asyncio
async def test_totp_login_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.8 — zitadel 5xx → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))
    temp_token = _put_pending(failures=0)
    body = TOTPLoginRequest(temp_token=temp_token, code="123456", auth_request_id="ar-5")
    db = AsyncMock(spec=AsyncSession)
    response = MagicMock()

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await totp_login(body=body, response=response, db=db)

    assert exc.value.status_code == 502
    audit_log.assert_not_called()  # 5xx is not "invalid_code" — no audit
    events = _capture_events(captured, "totp_login_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502
    assert events[0]["outcome"] == "502"
    assert events[0]["log_level"] == "error"
    _pending_totp.pop(temp_token)  # cleanup
