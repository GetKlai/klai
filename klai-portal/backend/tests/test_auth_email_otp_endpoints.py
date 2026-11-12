"""SPEC-SEC-AUTH-COVERAGE-001 REQ-1.11..1.14 — email_otp endpoints + tests.

Email-OTP MFA flow: setup, confirm, resend. Zitadel v4 activates the e-mail
code factor on ``POST /v2/users/{id}/otp_email`` and mails nothing; a code is
mailed only by a session challenge. Setup and resend therefore register the
factor, open a user session and challenge it, and confirm checks the code on
that session. tests/contract/test_zitadel_login_contract.py runs the same flow
against the pinned Zitadel image; here Zitadel is mocked with respx.
"""

from __future__ import annotations

import json
from typing import Any

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

_PENDING_KEY = "email_otp_enrolment:uid-1"


def _mock_enrolment(router: respx.MockRouter, register_status: int = 200) -> dict[str, respx.Route]:
    return {
        "register": router.post("/v2/users/uid-1/otp_email").mock(
            return_value=httpx.Response(register_status, json={})
        ),
        "session": router.post("/v2/sessions").mock(
            return_value=httpx.Response(201, json={"sessionId": "sess-1", "sessionToken": "tok-1"})
        ),
        "challenge": router.patch("/v2/sessions/sess-1").mock(
            return_value=httpx.Response(200, json={"sessionToken": "tok-challenged"})
        ),
    }


# ---------------------------------------------------------------------------
# email_otp_setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_otp_setup_mails_a_code_through_a_session_challenge(
    respx_zitadel: respx.MockRouter, fake_redis: Any
) -> None:
    """REQ-1.11 — setup registers the factor and has Zitadel mail a code for it."""
    routes = _mock_enrolment(respx_zitadel)

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await email_otp_setup(user_id="uid-1")

    assert routes["register"].call_count == 1
    session = json.loads(routes["session"].calls.last.request.content)
    assert session == {"checks": {"user": {"userId": "uid-1"}}, "lifetime": "300s"}
    assert json.loads(routes["challenge"].calls.last.request.content) == {
        "sessionToken": "tok-1",
        "challenges": {"otpEmail": {"sendCode": {}}},
    }
    assert json.loads(await fake_redis.get(_PENDING_KEY)) == {"session_id": "sess-1", "session_token": "tok-challenged"}
    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.email-otp.setup"
    assert _capture_events(captured, "email_otp_setup_failed") == []


@pytest.mark.asyncio
async def test_email_otp_setup_for_an_already_registered_factor_still_mails_a_code(
    respx_zitadel: respx.MockRouter, fake_redis: Any
) -> None:
    """Zitadel answers 409 to a second registration; the user still gets a code."""
    routes = _mock_enrolment(respx_zitadel, register_status=409)

    with _audit_log_patch():
        await email_otp_setup(user_id="uid-1")

    assert routes["challenge"].call_count == 1
    assert await fake_redis.get(_PENDING_KEY)


@pytest.mark.asyncio
async def test_email_otp_setup_zitadel_5xx(respx_zitadel: respx.MockRouter, fake_redis: Any) -> None:
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
    assert await fake_redis.get(_PENDING_KEY) is None


# ---------------------------------------------------------------------------
# email_otp_confirm
# ---------------------------------------------------------------------------


async def _pending(fake_redis: Any) -> None:
    await fake_redis.set(_PENDING_KEY, json.dumps({"session_id": "sess-1", "session_token": "tok-challenged"}))


@pytest.mark.asyncio
async def test_email_otp_confirm_checks_the_code_on_the_enrolment_session(
    respx_zitadel: respx.MockRouter, fake_redis: Any
) -> None:
    """REQ-1.12 — confirm verifies the mailed code as an ``otpEmail`` session check."""
    await _pending(fake_redis)
    check = respx_zitadel.patch("/v2/sessions/sess-1").mock(
        return_value=httpx.Response(200, json={"sessionToken": "tok-checked"})
    )

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await email_otp_confirm(body=EmailOTPConfirmRequest(code="12345678"), user_id="uid-1")

    assert json.loads(check.calls.last.request.content) == {
        "sessionToken": "tok-challenged",
        "checks": {"otpEmail": {"code": "12345678"}},
    }
    assert await fake_redis.get(_PENDING_KEY) is None
    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.email-otp.confirmed"
    assert _capture_events(captured, "email_otp_confirm_failed") == []


@pytest.mark.asyncio
async def test_email_otp_confirm_invalid_code(respx_zitadel: respx.MockRouter, fake_redis: Any) -> None:
    """REQ-1.12 — email_otp_confirm 4xx → 400 + event; the code can be retried."""
    await _pending(fake_redis)
    respx_zitadel.patch("/v2/sessions/sess-1").mock(return_value=httpx.Response(400, json={"code": 3}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await email_otp_confirm(body=EmailOTPConfirmRequest(code="00000000"), user_id="uid-1")

    assert exc.value.status_code == 400
    audit_log.assert_not_called()
    events = _capture_events(captured, "email_otp_confirm_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "invalid_code"
    assert events[0]["outcome"] == "400"
    assert await fake_redis.get(_PENDING_KEY)


@pytest.mark.asyncio
async def test_email_otp_confirm_without_a_sent_code_is_refused(
    respx_zitadel: respx.MockRouter, fake_redis: Any
) -> None:
    """No code was sent, or it expired: 400 without calling Zitadel."""
    check = respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await email_otp_confirm(body=EmailOTPConfirmRequest(code="12345678"), user_id="uid-1")

    assert exc.value.status_code == 400
    assert check.call_count == 0
    audit_log.assert_not_called()
    assert _capture_events(captured, "email_otp_confirm_failed")[0]["reason"] == "expired"


@pytest.mark.asyncio
async def test_email_otp_confirm_zitadel_5xx(respx_zitadel: respx.MockRouter, fake_redis: Any) -> None:
    """REQ-1.12 — email_otp_confirm 5xx → 502 + event."""
    await _pending(fake_redis)
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await email_otp_confirm(body=EmailOTPConfirmRequest(code="12345678"), user_id="uid-1")

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
async def test_email_otp_resend_mails_a_new_code(respx_zitadel: respx.MockRouter, fake_redis: Any) -> None:
    """REQ-1.13 — resend keeps the factor and challenges a fresh session."""
    await _pending(fake_redis)
    routes = _mock_enrolment(respx_zitadel, register_status=409)
    remove = respx_zitadel.delete("/v2/users/uid-1/otp_email").mock(return_value=httpx.Response(200, json={}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await email_otp_resend(user_id="uid-1")

    assert remove.call_count == 0
    assert routes["challenge"].call_count == 1
    assert json.loads(await fake_redis.get(_PENDING_KEY))["session_token"] == "tok-challenged"
    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.email-otp.resent"
    assert _capture_events(captured, "email_otp_resend_failed") == []


@pytest.mark.asyncio
async def test_email_otp_resend_zitadel_5xx(respx_zitadel: respx.MockRouter, fake_redis: Any) -> None:
    """REQ-1.13 — email_otp_resend 5xx → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await email_otp_resend(user_id="uid-1")

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "email_otp_resend_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
