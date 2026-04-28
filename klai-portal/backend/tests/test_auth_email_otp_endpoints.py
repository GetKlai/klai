"""SPEC-SEC-AUTH-COVERAGE-001 REQ-1.11..1.14 — email_otp endpoints + tests.

Email-OTP MFA flow: setup, confirm, resend. Same brute-force surface as
totp_confirm (6-digit code) — fail-open under Zitadel 5xx is the same
class of risk that motivated SPEC-SEC-MFA-001.

Zitadel HTTP mocked via respx; no MagicMock on ``app.api.auth.zitadel``.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from auth_test_helpers import _audit_log_patch, _capture_events
from fastapi import HTTPException
from structlog.testing import capture_logs

from app.api.auth import (
    EmailOTPConfirmRequest,
    email_otp_confirm,
    email_otp_resend,
    email_otp_setup,
)

# ---------------------------------------------------------------------------
# email_otp_setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_otp_setup_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.11 — email_otp_setup happy."""
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await email_otp_setup(user_id="uid-1")

    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.email-otp.setup"
    assert _capture_events(captured, "email_otp_setup_failed") == []


@pytest.mark.asyncio
async def test_email_otp_setup_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.11 — email_otp_setup 5xx → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await email_otp_setup(user_id="uid-1")

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "email_otp_setup_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502


# ---------------------------------------------------------------------------
# email_otp_confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_otp_confirm_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.12 — email_otp_confirm happy."""
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await email_otp_confirm(body=EmailOTPConfirmRequest(code="123456"), user_id="uid-1")

    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.email-otp.confirmed"
    assert _capture_events(captured, "email_otp_confirm_failed") == []


@pytest.mark.asyncio
async def test_email_otp_confirm_invalid_code(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.12 — email_otp_confirm 4xx → 400 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(400, json={"error": "bad code"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await email_otp_confirm(body=EmailOTPConfirmRequest(code="000000"), user_id="uid-1")

    assert exc.value.status_code == 400
    audit_log.assert_not_called()
    events = _capture_events(captured, "email_otp_confirm_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "invalid_code"
    assert events[0]["outcome"] == "400"


@pytest.mark.asyncio
async def test_email_otp_confirm_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.12 — email_otp_confirm 5xx → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await email_otp_confirm(body=EmailOTPConfirmRequest(code="123456"), user_id="uid-1")

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "email_otp_confirm_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["outcome"] == "502"


# ---------------------------------------------------------------------------
# email_otp_resend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_otp_resend_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.13 — email_otp_resend happy."""
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await email_otp_resend(user_id="uid-1")

    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.email-otp.resent"
    assert _capture_events(captured, "email_otp_resend_failed") == []


@pytest.mark.asyncio
async def test_email_otp_resend_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.13 — email_otp_resend 5xx on remove → 502 + event.

    Note: 404 on remove is treated as benign (not registered yet); the
    handler proceeds to register. Only non-404 5xx fail-closes.
    """
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await email_otp_resend(user_id="uid-1")

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "email_otp_resend_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
