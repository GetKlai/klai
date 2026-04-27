"""SPEC-SEC-MFA-001 fail-closed regression suite.

Verifies that ``login()`` rejects with HTTP 503 + ``Retry-After: 5`` whenever
the MFA enforcement check cannot be completed under
``mfa_policy == "required"``. Preserves fail-open behaviour under
``mfa_policy == "optional"`` (deliberate trade-off documented in spec.md).

All Zitadel HTTP calls are mocked via ``respx`` against the real
``ZitadelClient`` instance — never via ``MagicMock`` on the module attribute
(REQ-5.7). This catches regressions in the client wrapper itself.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from auth_test_helpers import (
    _audit_emit_patches,
    _expected_email_hash,
    _make_db_mock,
    _make_login_body,
    _mfa_events,
    _session_ok,
)
from fastapi import HTTPException
from structlog.testing import capture_logs

from app.api.auth import login

# `respx_zitadel` is auto-discovered as a fixture via tests/conftest.py
# (re-exported from auth_test_helpers). No import needed here.

# ---------------------------------------------------------------------------
# Scenario 1 — mfa_policy=required + has_any_mfa 500 → 503 (REQ-1.1, 1.3, 1.4, 1.5, 4.1, 4.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_has_any_mfa_500_returns_503(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-req", "details": {"resourceOwner": "zorg-req"}}]}
        )
    )
    respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
        return_value=httpx.Response(500, json={"error": "internal"})
    )
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))

    db = _make_db_mock(org_mfa_policy="required")
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await login(body=_make_login_body(), response=response, db=db)

    assert exc.value.status_code == 503
    assert exc.value.headers["Retry-After"] == "5"
    assert "temporarily unavailable" in str(exc.value.detail).lower()
    response.set_cookie.assert_not_called()  # REQ-1.5 — no session artefact on this path

    events = _mfa_events(captured)
    assert len(events) == 1
    assert events[0]["reason"] == "has_any_mfa_5xx"
    assert events[0]["mfa_policy"] == "required"
    assert events[0]["zitadel_status"] == 500
    assert events[0]["outcome"] == "503"
    assert events[0]["log_level"] == "error"
    assert events[0]["email_hash"] == _expected_email_hash()


# ---------------------------------------------------------------------------
# Scenario 2 — find_user_by_email 500 → 503 (REQ-2.1, 2.4(b), 2.5, 4.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_user_by_email_500_returns_503(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post("/v2/users").mock(return_value=httpx.Response(500, json={"error": "internal"}))
    sessions = respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))

    db = _make_db_mock()
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await login(body=_make_login_body(), response=response, db=db)

    assert exc.value.status_code == 503
    assert exc.value.headers["Retry-After"] == "5"
    assert sessions.call_count == 0  # REQ-2.5 — create_session_with_password never invoked
    response.set_cookie.assert_not_called()

    events = _mfa_events(captured)
    assert len(events) == 1
    assert events[0]["reason"] == "find_user_by_email_5xx"
    assert events[0]["mfa_policy"] == "unresolved"
    assert events[0]["zitadel_status"] == 500
    assert events[0]["outcome"] == "503"
    assert events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# Scenario 3 — mfa_policy=optional + has_any_mfa 500 → 200 documented fail-open
# (REQ-3.1, 3.6 — short-circuit means has_any_mfa is not called under optional)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optional_has_any_mfa_500_proceeds_no_event(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-opt", "details": {"resourceOwner": "zorg-opt"}}]}
        )
    )
    # has_totp + has_any_mfa share this URL. Return 200 so has_totp succeeds with no enrolment.
    auth_methods = respx_zitadel.get("/v2/users/uid-opt/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": []})
    )
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": "https://chat.getklai.com/cb"})
    )

    db = _make_db_mock(portal_user_org_id=11, portal_user_zitadel_id="uid-opt", org_mfa_policy="optional")
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        result = await login(body=_make_login_body(), response=response, db=db)

    assert result is not None
    # Under mfa_policy="optional" the `if mfa_policy == "required":` guard
    # short-circuits has_any_mfa, so the call is never attempted and no
    # mfa_check_failed event is emitted (acceptance.md Scenario 3 clarification).
    assert _mfa_events(captured) == []
    # has_totp is the only call to /authentication_methods (one call, returns 200)
    assert auth_methods.call_count == 1


# ---------------------------------------------------------------------------
# Scenario 4 — Happy path MFA login (REQ-5.2(d))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_required_with_totp_enrolled_returns_totp_required(
    respx_zitadel: respx.MockRouter,
) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-req", "details": {"resourceOwner": "zorg-req"}}]}
        )
    )
    respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": ["AUTHENTICATION_METHOD_TYPE_TOTP"]})
    )
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))

    db = _make_db_mock(org_mfa_policy="required")
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        result = await login(body=_make_login_body(), response=response, db=db)

    assert result.status == "totp_required"
    assert result.temp_token  # non-empty opaque token
    response.set_cookie.assert_not_called()  # cookie minted on totp-login completion, not here
    assert _mfa_events(captured) == []


# ---------------------------------------------------------------------------
# Scenario 5 — Happy path no-MFA under optional (REQ-5.2(e))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_optional_no_mfa_sets_cookie(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-opt", "details": {"resourceOwner": "zorg-opt"}}]}
        )
    )
    respx_zitadel.get("/v2/users/uid-opt/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": []})
    )
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": "https://chat.getklai.com/cb"})
    )

    db = _make_db_mock(portal_user_org_id=11, portal_user_zitadel_id="uid-opt", org_mfa_policy="optional")
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        result = await login(body=_make_login_body(), response=response, db=db)

    assert result.status == "ok"
    response.set_cookie.assert_called_once()  # session cookie minted by _finalize_and_set_cookie
    assert _mfa_events(captured) == []


# ---------------------------------------------------------------------------
# Scenario 6 — find_user_by_email returns no results → continue to 401 (REQ-2.3, 5.2(f))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_user_by_email_no_results_continues_to_password_check(
    respx_zitadel: respx.MockRouter,
) -> None:
    """Zitadel returns ``{"result": []}`` for unknown users — well-formed not-found,
    not infrastructure failure. Login proceeds to password-check which fails 401.
    """
    respx_zitadel.post("/v2/users").mock(return_value=httpx.Response(200, json={"result": []}))
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(401, json={"error": "invalid credentials"}))

    db = _make_db_mock()
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await login(body=_make_login_body(), response=response, db=db)

    assert exc.value.status_code == 401
    assert "incorrect" in str(exc.value.detail).lower()
    assert _mfa_events(captured) == []


# ---------------------------------------------------------------------------
# Scenario 7 — portal_user found + org fetch raises → 503 (REQ-3.2, 5.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portal_user_found_org_fetch_raises_returns_503(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-req", "details": {"resourceOwner": "zorg-req"}}]}
        )
    )
    respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": []})
    )
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))

    db = _make_db_mock(
        org_mfa_policy=None,
        get_side_effect=RuntimeError("RLS: app.current_org_id is not set"),
    )
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await login(body=_make_login_body(), response=response, db=db)

    assert exc.value.status_code == 503
    assert exc.value.headers["Retry-After"] == "5"
    response.set_cookie.assert_not_called()

    events = _mfa_events(captured)
    assert len(events) == 1
    assert events[0]["reason"] == "db_lookup_failed"
    assert events[0]["mfa_policy"] == "unresolved"
    assert events[0]["outcome"] == "503"
    assert events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# Scenario 7a — portal_user lookup raises → fail-open optional (REQ-3.2, 5.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portal_user_lookup_raises_proceeds_documented_fail_open(
    respx_zitadel: respx.MockRouter,
) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-req", "details": {"resourceOwner": "zorg-req"}}]}
        )
    )
    respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": []})
    )
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": "https://chat.getklai.com/cb"})
    )

    db = _make_db_mock(scalar_side_effect=RuntimeError("DB connection lost"))
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        result = await login(body=_make_login_body(), response=response, db=db)

    assert result is not None
    response.set_cookie.assert_called_once()

    events = _mfa_events(captured)
    assert len(events) == 1
    assert events[0]["reason"] == "db_lookup_failed"
    assert events[0]["mfa_policy"] == "optional"
    assert events[0]["outcome"] == "fail-open"
    assert events[0]["log_level"] == "warning"


# ---------------------------------------------------------------------------
# Scenario 8 — has_any_mfa RequestError → 503 (REQ-1.2, 5.2(a)-variant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_has_any_mfa_request_error_returns_503(respx_zitadel: respx.MockRouter) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-req", "details": {"resourceOwner": "zorg-req"}}]}
        )
    )
    respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))

    db = _make_db_mock(org_mfa_policy="required")
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await login(body=_make_login_body(), response=response, db=db)

    assert exc.value.status_code == 503
    assert exc.value.headers["Retry-After"] == "5"

    events = _mfa_events(captured)
    assert len(events) == 1
    assert events[0]["reason"] == "has_any_mfa_5xx"
    assert events[0]["zitadel_status"] is None  # no response body to read
    assert events[0]["outcome"] == "503"
    assert events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# Scenario 7b — orphan PortalOrg FK → fail-open + emit warning
# Pre-existing silent fall-back was hiding data-integrity bugs (e.g. soft-
# deleted org). We keep fail-open semantics but make it observable.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portal_user_orphan_org_proceeds_documented_fail_open(
    respx_zitadel: respx.MockRouter,
) -> None:
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-orphan", "details": {"resourceOwner": "zorg-orphan"}}]}
        )
    )
    respx_zitadel.get("/v2/users/uid-orphan/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": []})
    )
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": "https://chat.getklai.com/cb"})
    )

    # portal_user exists, but db.get(PortalOrg, ...) returns None (orphan FK).
    db = _make_db_mock(
        portal_user_org_id=99,
        portal_user_zitadel_id="uid-orphan",
        org_mfa_policy=None,  # forces db.get to return None
    )
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        result = await login(body=_make_login_body(), response=response, db=db)

    # Login succeeds (fail-open) and a session cookie is minted
    assert result is not None
    response.set_cookie.assert_called_once()

    events = _mfa_events(captured)
    assert len(events) == 1
    assert events[0]["reason"] == "db_lookup_failed"
    assert events[0]["mfa_policy"] == "optional"
    assert events[0]["outcome"] == "fail-open"
    assert events[0]["log_level"] == "warning"


# ---------------------------------------------------------------------------
# Run-phase addition (REQ-1.6) — unexpected exception type still fails closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_has_any_mfa_generic_exception_returns_503(respx_zitadel: respx.MockRouter) -> None:
    """REQ-1.6 — any non-httpx exception during has_any_mfa still fail-closes."""
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-req", "details": {"resourceOwner": "zorg-req"}}]}
        )
    )
    # Bypass respx by patching the client method directly with a non-httpx exception.
    respx_zitadel.get("/v2/users/uid-req/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": []})  # has_totp succeeds
    )
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))

    db = _make_db_mock(org_mfa_policy="required")
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    # Patch the bound method on the singleton (still does NOT mock app.api.auth.zitadel,
    # only swaps a single coroutine at the client level — REQ-5.7 compliant in spirit).
    from app.services.zitadel import zitadel as zitadel_singleton

    with (
        capture_logs() as captured,
        audit_patch,
        emit_patch,
        patch.object(zitadel_singleton, "has_any_mfa", AsyncMock(side_effect=RuntimeError("kernel panic"))),
    ):
        with pytest.raises(HTTPException) as exc:
            await login(body=_make_login_body(), response=response, db=db)

    assert exc.value.status_code == 503
    assert exc.value.headers["Retry-After"] == "5"

    events = _mfa_events(captured)
    assert len(events) == 1
    assert events[0]["reason"] == "unexpected"
    assert events[0]["mfa_policy"] == "required"
    assert events[0]["zitadel_status"] is None
    assert events[0]["outcome"] == "503"
    assert events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# Run-phase addition (REQ-2.2) — find_user_by_email RequestError → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_user_by_email_request_error_returns_503(respx_zitadel: respx.MockRouter) -> None:
    """REQ-2.2 — connection failure during find_user_by_email still fail-closes."""
    respx_zitadel.post("/v2/users").mock(side_effect=httpx.ConnectError("Connection refused"))
    sessions = respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))

    db = _make_db_mock()
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await login(body=_make_login_body(), response=response, db=db)

    assert exc.value.status_code == 503
    assert exc.value.headers["Retry-After"] == "5"
    assert sessions.call_count == 0  # password check not attempted

    events = _mfa_events(captured)
    assert len(events) == 1
    assert events[0]["reason"] == "find_user_by_email_5xx"
    assert events[0]["mfa_policy"] == "unresolved"
    assert events[0]["zitadel_status"] is None
    assert events[0]["outcome"] == "503"
    assert events[0]["log_level"] == "error"


# ---------------------------------------------------------------------------
# Run-phase addition (REQ-3.4) — mfa_policy="recommended" behaves like optional
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recommended_policy_behaves_like_optional(respx_zitadel: respx.MockRouter) -> None:
    """REQ-3.4 — `recommended` is a UI-only hint; at login time it does NOT enforce."""
    respx_zitadel.post("/v2/users").mock(
        return_value=httpx.Response(
            200, json={"result": [{"userId": "uid-rec", "details": {"resourceOwner": "zorg-rec"}}]}
        )
    )
    auth_methods = respx_zitadel.get("/v2/users/uid-rec/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": []})
    )
    # has_any_mfa would fail if called — proves recommended skips it.
    respx_zitadel.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": "https://chat.getklai.com/cb"})
    )

    db = _make_db_mock(portal_user_org_id=12, portal_user_zitadel_id="uid-rec", org_mfa_policy="recommended")
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        result = await login(body=_make_login_body(), response=response, db=db)

    assert result.status == "ok"
    response.set_cookie.assert_called_once()
    # has_totp called once (probes UI flag); has_any_mfa NOT called under recommended.
    assert auth_methods.call_count == 1
    assert _mfa_events(captured) == []
