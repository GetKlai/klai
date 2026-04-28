"""SPEC-SEC-AUTH-COVERAGE-001 REQ-1.9/1.10/1.14 — passkey endpoints + tests.

WebAuthn passkey enrolment via Zitadel: ``passkey_setup`` returns
PublicKeyCredentialCreationOptions; ``passkey_confirm`` validates the
attestation. Same Zitadel-5xx fail-mode as TOTP enrolment.

Zitadel HTTP mocked via respx; no MagicMock on ``app.api.auth.zitadel``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
import respx
from auth_test_helpers import _audit_log_patch, _capture_events
from fastapi import HTTPException
from structlog.testing import capture_logs

from app.api.auth import PasskeyConfirmRequest, passkey_confirm, passkey_setup


def _make_request_mock() -> MagicMock:
    """Mock FastAPI Request with a headers dict so ``request.headers.get()`` works."""
    request = MagicMock()
    request.headers = {"host": "my.getklai.com"}
    return request


# ---------------------------------------------------------------------------
# passkey_setup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passkey_setup_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.9 — passkey_setup happy path."""
    respx_zitadel.route().mock(
        return_value=httpx.Response(
            200,
            json={
                "passkeyId": "pk-1",
                "publicKeyCredentialCreationOptions": {"challenge": "abc"},
            },
        )
    )

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        result = await passkey_setup(request=_make_request_mock(), user_id="uid-1")

    assert result.passkey_id == "pk-1"
    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.passkey.setup"
    assert _capture_events(captured, "passkey_setup_failed") == []


@pytest.mark.asyncio
async def test_passkey_setup_zitadel_5xx(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.9 — passkey_setup 5xx → 502 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(502, json={"error": "bad gw"}))

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await passkey_setup(request=_make_request_mock(), user_id="uid-1")

    assert exc.value.status_code == 502
    audit_log.assert_not_called()
    events = _capture_events(captured, "passkey_setup_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "zitadel_5xx"
    assert events[0]["zitadel_status"] == 502
    assert events[0]["outcome"] == "502"


# ---------------------------------------------------------------------------
# passkey_confirm
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passkey_confirm_happy(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.10 — passkey_confirm happy path."""
    respx_zitadel.route().mock(return_value=httpx.Response(200, json={}))
    body = PasskeyConfirmRequest(
        passkey_id="pk-1",
        public_key_credential={"id": "cred-1", "type": "public-key"},
        passkey_name="iPhone",
    )

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        await passkey_confirm(body=body, user_id="uid-1")

    audit_log.assert_called_once()
    assert audit_log.call_args.kwargs["action"] == "auth.passkey.confirmed"
    assert _capture_events(captured, "passkey_confirm_failed") == []


@pytest.mark.asyncio
async def test_passkey_confirm_invalid_attestation(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.10 — passkey_confirm 4xx → 400 + event."""
    respx_zitadel.route().mock(return_value=httpx.Response(400, json={"error": "bad attestation"}))
    body = PasskeyConfirmRequest(
        passkey_id="pk-1",
        public_key_credential={"id": "cred-bad"},
        passkey_name="iPhone",
    )

    with capture_logs() as captured, _audit_log_patch() as audit_log:
        with pytest.raises(HTTPException) as exc:
            await passkey_confirm(body=body, user_id="uid-1")

    assert exc.value.status_code == 400
    audit_log.assert_not_called()
    events = _capture_events(captured, "passkey_confirm_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "invalid_attestation"
    assert events[0]["zitadel_status"] == 400
    assert events[0]["outcome"] == "400"
    assert events[0]["log_level"] == "warning"
