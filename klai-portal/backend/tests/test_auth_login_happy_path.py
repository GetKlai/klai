"""SPEC-SEC-SESSION-001 acceptance scenario 5 — REQ-6.5.

Full happy-path regression: password → TOTP success → SSO cookie. Asserts
the Redis-backed pending state lifecycle, including the four bound fields
(session_id, session_token, ua_hash, ip_subnet) and the failure counter
that starts at zero and is deleted on success. The pre-SPEC in-memory
``TTLCache`` flow had no equivalent assertions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import Response
from helpers import make_request
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.api.auth import (
    _TOTP_PENDING_FAILURES_PREFIX,
    _TOTP_PENDING_KEY_PREFIX,
    LoginRequest,
    TOTPLoginRequest,
    login,
    totp_login,
)


def _session_ok() -> dict[str, str]:
    return {"sessionId": "sess-happy", "sessionToken": "tok-happy"}


async def test_password_totp_login_happy_path(fake_redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Step-by-step trace of the dual-call login flow.

    Assertions cover the SPEC-SEC-SESSION-001 invariants:
    - REQ-1.1: pending hash holds all four bound fields after /login.
    - REQ-1.4-implied: counter exists and starts at 0.
    - REQ-1.6: both keys are gone after the successful /totp-login.
    - REQ-2.3: ``ip_subnet`` is the /24 network address.
    - REQ-5: no lockout / redis-unavailable events are emitted on the
      happy path.
    """
    # -- Step 1: POST /api/auth/login --------------------------------------
    body = LoginRequest(email="alice@acme.com", password="correct horse", auth_request_id="ar-happy-1")
    response = MagicMock(spec=Response)
    db = AsyncMock(spec=AsyncSession)

    request = make_request(
        headers={
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "x-forwarded-for": "203.0.113.42",
        }
    )

    with (
        capture_logs() as captured,
        patch("app.api.auth.zitadel") as mock_zitadel,
        patch("app.api.auth.audit") as mock_audit,
        patch("app.api.auth.emit_event"),
        patch("app.api.auth._resolve_and_enforce_mfa", AsyncMock(return_value=None)),
    ):
        mock_zitadel.find_user_by_email = AsyncMock(return_value=("uid-happy", "zorg-happy"))
        mock_zitadel.has_totp = AsyncMock(return_value=True)
        mock_zitadel.create_session_with_password = AsyncMock(return_value=_session_ok())
        mock_audit.log_event = AsyncMock()

        login_result = await login(body=body, response=response, request=request, db=db)

    assert login_result.status == "totp_required"
    assert login_result.temp_token  # urlsafe-base64, non-empty
    temp_token = login_result.temp_token

    # -- Redis state after step 1 -----------------------------------------
    state_key = f"{_TOTP_PENDING_KEY_PREFIX}{temp_token}"
    counter_key = f"{_TOTP_PENDING_FAILURES_PREFIX}{temp_token}"

    state = await fake_redis.hgetall(state_key)
    assert state["session_id"] == "sess-happy"
    assert state["session_token"] == "tok-happy"
    # SHA-256 hex of the UA → 64 hex chars; non-empty proves hash_metadata ran.
    assert len(state["ua_hash"]) == 64
    # /24 network address for 203.0.113.42 is 203.0.113.0
    assert state["ip_subnet"] == "203.0.113.0"

    counter = await fake_redis.get(counter_key)
    assert counter == "0", f"failure counter must start at 0, got {counter!r}"

    # -- Step 2: POST /api/auth/totp-login --------------------------------
    totp_body = TOTPLoginRequest(temp_token=temp_token, code="123456", auth_request_id="ar-happy-1")

    with (
        capture_logs() as captured_totp,
        patch("app.api.auth.zitadel") as mock_zitadel,
        patch("app.api.auth.audit") as mock_audit,
        patch("app.api.auth._finalize_and_set_cookie") as mock_finalize,
    ):
        mock_zitadel.update_session_with_totp = AsyncMock(
            return_value={"sessionId": "sess-happy", "sessionToken": "tok-happy-renewed"}
        )
        mock_audit.log_event = AsyncMock()
        # Bypass the cookie-set + auth-finalize round trip; this test asserts
        # the pending-state lifecycle, not the SSO cookie shape (covered by
        # the dedicated SSO tests).
        mock_finalize.return_value = MagicMock(status="success")

        totp_result = await totp_login(body=totp_body, response=Response(), db=db)

    assert totp_result is not None

    # -- Redis state after step 2 — both keys gone (REQ-1.6) -------------
    assert await fake_redis.exists(state_key) == 0
    assert await fake_redis.exists(counter_key) == 0

    # -- No noise on the happy path (REQ-5) ------------------------------
    happy_path_events = {e.get("event") for e in captured + captured_totp}
    assert "totp_pending_lockout" not in happy_path_events
    assert "totp_pending_redis_unavailable" not in happy_path_events
