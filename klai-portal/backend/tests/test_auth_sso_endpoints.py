"""SPEC-SEC-AUTH-COVERAGE-001 REQ-4 — sso_complete observability + tests.

Failure-leg coverage for ``POST /api/auth/sso-complete``: missing cookie,
tampered cookie, finalize 5xx. Success path is intentionally silent —
cookie reuse is non-interactive UX, not audited (REQ-4.4).

All Zitadel HTTP calls mocked via respx against the real
``ZitadelClient``; no ``MagicMock`` on ``app.api.auth.zitadel`` (REQ-5.7).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from auth_test_helpers import (
    _audit_emit_patches,
    _capture_events,
    _make_sso_cookie,
)
from fastapi import HTTPException
from structlog.testing import capture_logs

from app.api.auth import SSOCompleteRequest, sso_complete

# ---------------------------------------------------------------------------
# Scenario S1 — happy path (REQ-4.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_complete_happy_path(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": "https://chat.getklai.com/cb"})
    )
    body = SSOCompleteRequest(auth_request_id="ar-sso-1")
    cookie = _make_sso_cookie()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        result = await sso_complete(body=body, klai_sso=cookie)

    assert result.callback_url == "https://chat.getklai.com/cb"
    # REQ-4.4: SSO success is silent — no audit log, no failure event
    assert _capture_events(captured, "sso_complete_failed") == []


# ---------------------------------------------------------------------------
# Scenario S2 — missing cookie (REQ-4.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_complete_missing_cookie(respx_zitadel: respx.MockRouter) -> None:
    body = SSOCompleteRequest(auth_request_id="ar-sso-2")
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await sso_complete(body=body, klai_sso=None)

    assert exc.value.status_code == 401
    assert exc.value.detail == "No SSO session"

    events = _capture_events(captured, "sso_complete_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "no_cookie"
    assert events[0]["outcome"] == "401"
    assert events[0]["log_level"] == "warning"
    # Zitadel was not called
    assert respx_zitadel.calls.call_count == 0


# ---------------------------------------------------------------------------
# Scenario S3 — tampered/invalid cookie (REQ-4.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_complete_tampered_cookie(respx_zitadel: respx.MockRouter) -> None:
    body = SSOCompleteRequest(auth_request_id="ar-sso-3")
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await sso_complete(body=body, klai_sso="not-a-valid-fernet-token")

    assert exc.value.status_code == 401
    assert exc.value.detail == "SSO cookie invalid"

    events = _capture_events(captured, "sso_complete_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "cookie_invalid"
    assert events[0]["outcome"] == "401"
    assert events[0]["log_level"] == "warning"
    assert respx_zitadel.calls.call_count == 0


# ---------------------------------------------------------------------------
# Scenario S4 — finalize 5xx (REQ-4.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sso_complete_finalize_5xx(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(502, json={"error": "bad gateway"})
    )
    body = SSOCompleteRequest(auth_request_id="ar-sso-4")
    cookie = _make_sso_cookie()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await sso_complete(body=body, klai_sso=cookie)

    assert exc.value.status_code == 401
    assert exc.value.detail == "SSO session no longer valid"

    events = _capture_events(captured, "sso_complete_failed")
    assert len(events) == 1
    assert events[0]["reason"] == "session_expired"
    assert events[0]["zitadel_status"] == 502
    assert events[0]["outcome"] == "401"
    assert events[0]["log_level"] == "warning"
