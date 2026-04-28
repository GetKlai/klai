"""SPEC-SEC-SESSION-001 acceptance scenario 8 — REQ-2.2, REQ-5.1, REQ-5.2.

A consolidated PII guard for the structured events the SPEC adds:

- ``totp_pending_lockout`` (REQ-5.1)
- ``idp_pending_binding_mismatch`` (REQ-5.2)

The other two events (``totp_pending_redis_unavailable``, REQ-5.3 and
``sso_cookie_key_missing_startup_abort``, REQ-5.4) are exercised by their
own regression files (``test_auth_totp_lockout`` and
``test_startup_sso_key_guard``).

This module is the single place that fails loud if a future change adds
raw User-Agent strings, raw caller IPs, the full opaque token, or Zitadel
session credentials to any of these events.

Implementation note: tests intercept the structlog emit by patching the
module-level ``_slog`` proxy with a ``MagicMock``, then inspect the
``call_args``. ``structlog.testing.capture_logs`` is unreliable in the
full suite because an unrelated test (the CORS allowlist suite) calls
``structlog.reset_defaults()`` mid-run; the cached lazy-proxy in
``app.api.auth`` then keeps emitting through the ``setup_logging``
processors instead of the temporary ``LogCapture`` chain. Patching
the proxy bypasses the cache entirely and keeps the assertion stable.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from helpers import make_request
from sqlalchemy.ext.asyncio import AsyncSession

# Sentinels we explicitly assert never appear in any captured event kwargs.
_UA_FIREFOX = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Firefox/127"
_UA_CURL = "curl/8.5.0"
_RAW_IPV4 = "203.0.113.42"
_RAW_IPV4_REPLAY = "198.51.100.7"
_SESSION_ID = "sess-pii-guard-9999"
_SESSION_TOKEN = "tok-pii-guard-zzzz"  # nosec — test placeholder


def _zitadel_400_error() -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        "wrong code",
        request=httpx.Request("POST", "https://example.invalid/v2/sessions"),
        response=httpx.Response(400, json={"message": "invalid"}),
    )


def _assert_no_pii(kwargs: dict[str, Any]) -> None:
    """Reject any structured-log kwarg that leaks UA, raw IP, session ids,
    or the full opaque token."""
    forbidden_substrings = [
        _UA_FIREFOX,
        _UA_CURL,
        _RAW_IPV4,
        _RAW_IPV4_REPLAY,
        _SESSION_ID,
        _SESSION_TOKEN,
    ]
    serialized = json.dumps(kwargs, default=repr)
    for needle in forbidden_substrings:
        assert needle not in serialized, (
            f"PII leak: {needle!r} appeared in event kwargs:\n{serialized}"
        )
    forbidden_keys = {"user_agent", "raw_ip", "client_ip", "session_id", "session_token"}
    leaked = forbidden_keys & set(kwargs.keys())
    assert not leaked, f"PII leak via kwarg(s) {leaked!r} in event {kwargs!r}"


# ---------------------------------------------------------------------------
# REQ-5.1 — totp_pending_lockout has no raw token / session ids
# ---------------------------------------------------------------------------


async def test_no_pii_in_totp_lockout_logs(
    fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive a token to the 5-failure lockout and inspect the structlog
    kwargs the production code passed to ``_slog.warning``."""
    from app.api.auth import TOTPLoginRequest, _totp_pending_create, totp_login

    temp_token = await _totp_pending_create(
        session_id=_SESSION_ID,
        session_token=_SESSION_TOKEN,
        ua_hash="",
        ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
    )
    monkeypatch.setattr(
        "app.api.auth.zitadel.update_session_with_totp",
        AsyncMock(side_effect=_zitadel_400_error()),
    )
    monkeypatch.setattr("app.api.auth.audit.log_event", AsyncMock())

    mock_slog = MagicMock()
    monkeypatch.setattr("app.api.auth._slog", mock_slog)

    body = TOTPLoginRequest(temp_token=temp_token, code="000000", auth_request_id="ar-pii")
    db = AsyncMock(spec=AsyncSession)

    for _ in range(5):
        try:
            await totp_login(body=body, response=Response(), db=db)
        except HTTPException:
            pass

    # Find the lockout call (others may exist for other unrelated emits)
    lockout_calls = [
        call for call in mock_slog.warning.call_args_list
        if call.args and call.args[0] == "totp_pending_lockout"
    ]
    assert len(lockout_calls) == 1, (
        f"expected 1 totp_pending_lockout emit, got {mock_slog.warning.call_args_list}"
    )

    kwargs = lockout_calls[0].kwargs
    assert kwargs["failures"] == 5
    # Token prefix only — never the full opaque token. The token is the
    # longest-lived in-memory secret in the TOTP flow.
    assert kwargs["token_prefix"] == temp_token[:8]
    assert temp_token not in json.dumps(kwargs, default=repr)

    _assert_no_pii(kwargs)


# ---------------------------------------------------------------------------
# REQ-5.2 — idp_pending_binding_mismatch carries prefixes only
# ---------------------------------------------------------------------------


def test_no_pii_in_binding_mismatch_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inspect the binding event captured when a stolen-cookie replay
    arrives from a different UA + a different IP /24."""
    from app.api.signup import _verify_idp_pending_binding
    from app.services.bff_session import SessionService

    mock_slog = MagicMock()
    monkeypatch.setattr("app.api.signup._slog", mock_slog)

    payload = {
        "session_id": _SESSION_ID,
        "session_token": _SESSION_TOKEN,
        "zitadel_user_id": "z-user-pii",
        "ua_hash": SessionService.hash_metadata(_UA_FIREFOX),
        "ip_subnet": "203.0.113.0",
    }
    request = make_request(
        headers={"user-agent": _UA_CURL, "x-forwarded-for": _RAW_IPV4_REPLAY},
    )

    with pytest.raises(HTTPException):
        _verify_idp_pending_binding(payload, request)

    mismatch_calls = [
        call for call in mock_slog.warning.call_args_list
        if call.args and call.args[0] == "idp_pending_binding_mismatch"
    ]
    assert len(mismatch_calls) == 1
    kwargs = mismatch_calls[0].kwargs
    # Prefixes are 8 chars of hex; never the full hash (correlatable with
    # the cookie value otherwise).
    assert len(kwargs["stored_ua_hash_prefix"]) == 8
    assert len(kwargs["current_ua_hash_prefix"]) == 8
    assert kwargs["stored_ua_hash_prefix"] != kwargs["current_ua_hash_prefix"]

    _assert_no_pii(kwargs)
