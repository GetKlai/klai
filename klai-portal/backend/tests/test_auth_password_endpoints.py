"""SPEC-SEC-AUTH-COVERAGE-001 REQ-3 — password_reset / password_set + tests.

Anti-enumeration is preserved: ``password_reset`` returns 204 on every
path. Failures emit ``password_reset_failed`` events for ops alerting,
``audit.log_event(action="auth.password.reset")`` is emitted on every
call so compliance can answer "who requested a reset on date X".

``password_set`` emits audit on success and ``password_set_failed`` on
4xx/5xx. Existing 400/502 status codes preserved.

Zitadel HTTP mocked via respx against the real ``ZitadelClient`` (REQ-5.7).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx
from auth_test_helpers import _capture_events, _expected_email_hash
from fastapi import HTTPException
from structlog.testing import capture_logs

from app.api.auth import (
    PasswordResetRequest,
    PasswordSetRequest,
    VerifyEmailRequest,
    password_reset,
    password_set,
    verify_email,
)


def _audit_log_patch() -> Any:
    """Return a ``patch()`` for audit.log_event yielding an assertable AsyncMock."""
    return patch("app.api.auth.audit.log_event", AsyncMock())


# ---------------------------------------------------------------------------
# Scenario P1 — password_reset known email → 204 + audit (REQ-3.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_reset_known_email(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-known", "details": {"resourceOwner": "zorg"}}]}
        )
    )
    respx_zitadel.post(url__regex=r"/v2/users/uid-known/password_reset.*").mock(
        return_value=httpx.Response(200, json={})
    )
    # Some Zitadel routes for find_user_id_by_email + send_password_reset
    # may be served via different exact paths. Mount a generic catch-all
    # to avoid unmocked-request failures on paths the test does not care about.
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))

    body = PasswordResetRequest(email="alice@acme.com")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await password_reset(body=body)

    audit_log.assert_called_once()
    call_kwargs = audit_log.call_args.kwargs
    assert call_kwargs["action"] == "auth.password.reset"
    assert call_kwargs["actor"] == "anonymous"
    assert call_kwargs["details"]["email_hash"] == _expected_email_hash("alice@acme.com")
    # No failure event on the happy path
    assert _capture_events(captured, "password_reset_failed") == []


# ---------------------------------------------------------------------------
# Scenario P2 — password_reset unknown email → 204 + audit + event (REQ-3.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_reset_unknown_email(respx_zitadel: respx.MockRouter) -> None:
    # Zitadel returns 200 with empty result for unknown emails (per find_user_id_by_email contract)
    respx_zitadel.post("/v2/users").mock(return_value=httpx.Response(200, json={"result": []}))
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))

    body = PasswordResetRequest(email="ghost@acme.com")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await password_reset(body=body)

    audit_log.assert_called_once()  # REQ-3.1: audit fires regardless of outcome
    events = _capture_events(captured, "password_reset_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "unknown_email"
    assert events[0]["email_hash"] == _expected_email_hash("ghost@acme.com")
    assert events[0]["outcome"] == "204"
    assert events[0]["log_level"] == "warning"


# ---------------------------------------------------------------------------
# Scenario P3 — password_reset find_user 5xx → 204 + event (REQ-3.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_reset_find_user_5xx(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post("/v2/users").mock(return_value=httpx.Response(502, json={"error": "bad gw"}))
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))

    body = PasswordResetRequest(email="alice@acme.com")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await password_reset(body=body)

    audit_log.assert_called_once()  # anti-enumeration preserves audit + 204 even on 5xx
    events = _capture_events(captured, "password_reset_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502
    assert events[0]["outcome"] == "204"
    assert events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# Scenario P4 — password_reset send_reset 5xx → 204 + event (REQ-3.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_reset_send_reset_5xx(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(200, json={"result": [{"userId": "uid-1", "details": {"resourceOwner": "z"}}]})
    )
    # send_password_reset uses POST /v2/users/{user_id}/password_reset (or similar)
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    body = PasswordResetRequest(email="alice@acme.com")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await password_reset(body=body)

    audit_log.assert_called_once()
    events = _capture_events(captured, "password_reset_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502
    assert events[0]["outcome"] == "204"


# ---------------------------------------------------------------------------
# Scenario P5 — password_set happy → 204 + audit (REQ-3.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_set_happy(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))

    body = PasswordSetRequest(user_id="uid-1", code="123456", new_password="NewSecret123!")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await password_set(body=body)

    audit_log.assert_called_once()
    call_kwargs = audit_log.call_args.kwargs
    assert call_kwargs["action"] == "auth.password.set"
    assert call_kwargs["actor"] == "uid-1"
    assert call_kwargs["details"]["reason"] == "set"
    assert _capture_events(captured, "password_set_failed") == []


# ---------------------------------------------------------------------------
# Scenario P6 — password_set expired link (410) → 400 + event (REQ-3.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_set_expired_link(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.route().mock(return_value=httpx.Response(410, json={"error": "code expired"}))

    body = PasswordSetRequest(user_id="uid-1", code="000000", new_password="NewSecret123!")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await password_set(body=body)

    assert exc.value.status_code == 400
    assert "expired or is invalid" in exc.value.detail
    audit_log.assert_not_called()  # Audit fires only on success
    events = _capture_events(captured, "password_set_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "expired_link"
    assert events[0]["zitadel_status"] == 410
    assert events[0]["actor_user_id"] == "uid-1"
    assert events[0]["outcome"] == "400"
    assert events[0]["log_level"] == "warning"


# ---------------------------------------------------------------------------
# Scenario P7 — password_set invalid code (400) → 400 + event (REQ-3.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_set_invalid_code(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.route().mock(return_value=httpx.Response(400, json={"error": "bad code"}))

    body = PasswordSetRequest(user_id="uid-1", code="wrong0", new_password="NewSecret123!")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await password_set(body=body)

    assert exc.value.status_code == 400
    audit_log.assert_not_called()
    events = _capture_events(captured, "password_set_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "invalid_code"
    assert events[0]["zitadel_status"] == 400
    assert events[0]["outcome"] == "400"


# ---------------------------------------------------------------------------
# Scenario P8 — password_set Zitadel 5xx → 502 + event (REQ-3.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_password_set_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    body = PasswordSetRequest(user_id="uid-1", code="123456", new_password="NewSecret123!")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await password_set(body=body)

    assert exc.value.status_code == 502
    assert "Failed to set password" in exc.value.detail
    audit_log.assert_not_called()
    events = _capture_events(captured, "password_set_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502
    assert events[0]["outcome"] == "502"
    assert events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# verify_email scenarios (REQ-3.8/3.9 — Cycle J extension)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_email_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-3.8 — verify_email happy path emits audit."""
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))
    body = VerifyEmailRequest(user_id="uid-1", code="123456", org_id="org-1")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await verify_email(body=body)

    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.email.verified"
    assert _capture_events(captured, "verify_email_failed") == []


@pytest.mark.asyncio
async def test_verify_email_invalid_code(respx_zitadel: respx.MockRouter) -> None:
    """REQ-3.8 — invalid code (400) → 400 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(400, json={"error": "bad code"}))
    body = VerifyEmailRequest(user_id="uid-1", code="000000", org_id="org-1")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await verify_email(body=body)

    assert exc.value.status_code == 400
    audit_log.assert_not_called()
    events = _capture_events(captured, "verify_email_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "invalid_code"
    assert events[0]["zitadel_status"] == 400
    assert events[0]["outcome"] == "400"


@pytest.mark.asyncio
async def test_verify_email_expired_link(respx_zitadel: respx.MockRouter) -> None:
    """REQ-3.8 — expired link (404) → 400 + event with reason=expired_link."""
    respx_zitadel.route().mock(return_value=httpx.Response(404, json={"error": "not found"}))
    body = VerifyEmailRequest(user_id="uid-1", code="123456", org_id="org-1")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await verify_email(body=body)

    assert exc.value.status_code == 400
    audit_log.assert_not_called()
    events = _capture_events(captured, "verify_email_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "expired_link"
    assert events[0]["zitadel_status"] == 404


@pytest.mark.asyncio
async def test_verify_email_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-3.8 — Zitadel 5xx → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))
    body = VerifyEmailRequest(user_id="uid-1", code="123456", org_id="org-1")

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await verify_email(body=body)

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "verify_email_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502
    assert events[0]["outcome"] == "502"
    assert events[0]["log_level"] == "error"
