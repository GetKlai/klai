"""A login completes only with every second factor the account presents.

The portal verifies a registered TOTP or e-mail code on the Zitadel session
before it finalises the auth request, and only those factors count as
enrolment under the organisation policy ``required``. Zitadel is mocked with
respx against the real ``ZitadelClient``, so the request shapes sent to the
Session API (v2, ``PATCH /v2/sessions/{id}``) are part of what is asserted.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from auth_test_helpers import (
    _audit_emit_patches,
    _make_db_mock,
    _make_login_body,
    _session_ok,
)
from fastapi import HTTPException
from helpers import make_request
from structlog.testing import capture_logs

from app.api.auth import TOTPLoginRequest, _totp_pending_create, login, totp_login

_CALLBACK = "http://localhost/api/auth/oidc/callback?code=c"
_TOTP = "AUTHENTICATION_METHOD_TYPE_TOTP"
_OTP_EMAIL = "AUTHENTICATION_METHOD_TYPE_OTP_EMAIL"


def _mock_user(router: respx.MockRouter, methods: list[str] | None = None, status: int = 200) -> respx.Route:
    router.post("/v2/users").mock(
        return_value=httpx.Response(200, json={"result": [{"userId": "uid-req", "details": {"resourceOwner": "zo"}}]})
    )
    router.post("/v2/sessions").mock(return_value=httpx.Response(200, json=_session_ok()))
    return router.get("/v2/users/uid-req/authentication_methods").mock(
        return_value=httpx.Response(status, json={"authMethodTypes": methods or []})
    )


@pytest.mark.asyncio
async def test_required_email_code_user_is_asked_for_the_code_before_finalising(
    respx_zitadel: respx.MockRouter, fake_redis: Any
) -> None:
    _mock_user(respx_zitadel, [_OTP_EMAIL])
    challenge = respx_zitadel.patch("/v2/sessions/sess-abc").mock(
        return_value=httpx.Response(200, json={"sessionToken": "tok-challenged", "challenges": {}})
    )
    finalize = respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": _CALLBACK})
    )
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with audit_patch, emit_patch:
        result = await login(body=_make_login_body(), response=response, request=make_request(), db=_make_db_mock())

    assert result.status == "otp_email_required"
    assert result.temp_token
    assert finalize.call_count == 0
    response.set_cookie.assert_not_called()
    # Zitadel session API v2 SetSession: RequestChallenges.otp_email.send_code
    # mails the code; the response carries the token that replaces the old one.
    assert json.loads(challenge.calls.last.request.content) == {
        "sessionToken": "tok-xyz",
        "challenges": {"otpEmail": {"sendCode": {}}},
    }
    pending = await fake_redis.hgetall(f"totp_pending:{result.temp_token}")
    assert (pending["session_token"], pending["check"]) == ("tok-challenged", "otpEmail")


@pytest.mark.asyncio
async def test_email_code_is_checked_on_the_session_before_finalising(
    respx_zitadel: respx.MockRouter, fake_redis: Any
) -> None:
    temp_token = await _totp_pending_create(
        session_id="sess-abc",
        session_token="tok-challenged",
        ua_hash="",
        ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
        check="otpEmail",
    )
    check = respx_zitadel.patch("/v2/sessions/sess-abc").mock(
        return_value=httpx.Response(200, json={"sessionToken": "tok-verified"})
    )
    finalize = respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": _CALLBACK})
    )
    response = MagicMock()
    audit_patch, emit_patch = _audit_emit_patches()

    with audit_patch, emit_patch:
        result = await totp_login(
            body=TOTPLoginRequest(temp_token=temp_token, code="12345678", auth_request_id="ar-1"),
            response=response,
            db=_make_db_mock(),
        )

    assert result.callback_url == _CALLBACK
    assert json.loads(check.calls.last.request.content) == {
        "sessionToken": "tok-challenged",
        "checks": {"otpEmail": {"code": "12345678"}},
    }
    assert json.loads(finalize.calls.last.request.content)["session"]["sessionToken"] == "tok-verified"


@pytest.mark.asyncio
async def test_pending_login_without_a_recorded_factor_is_checked_as_totp(
    respx_zitadel: respx.MockRouter, fake_redis: Any
) -> None:
    """A pending login stored before the factor was recorded came from a TOTP prompt."""
    temp_token = await _totp_pending_create(
        session_id="sess-abc",
        session_token="tok-xyz",
        ua_hash="",
        ip_subnet="0.0.0.0",  # noqa: S104 — placeholder, not a network bind
    )
    await fake_redis.hdel(f"totp_pending:{temp_token}", "check")
    check = respx_zitadel.patch("/v2/sessions/sess-abc").mock(
        return_value=httpx.Response(200, json={"sessionToken": "tok-verified"})
    )
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": _CALLBACK})
    )
    audit_patch, emit_patch = _audit_emit_patches()

    with audit_patch, emit_patch:
        await totp_login(
            body=TOTPLoginRequest(temp_token=temp_token, code="123456", auth_request_id="ar-1"),
            response=MagicMock(),
            db=_make_db_mock(),
        )

    assert json.loads(check.calls.last.request.content)["checks"] == {"totp": {"code": "123456"}}


@pytest.mark.asyncio
async def test_user_with_totp_and_email_code_is_asked_for_totp_without_a_mail(
    respx_zitadel: respx.MockRouter, fake_redis: Any
) -> None:
    _mock_user(respx_zitadel, [_OTP_EMAIL, _TOTP])
    challenge = respx_zitadel.patch("/v2/sessions/sess-abc").mock(return_value=httpx.Response(200, json={}))
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        result = await login(body=_make_login_body(), response=MagicMock(), request=make_request(), db=_make_db_mock())

    assert result.status == "totp_required"
    assert challenge.call_count == 0
    assert [e for e in captured if e.get("event") == "mfa_setup_required"] == []


@pytest.mark.asyncio
async def test_required_user_with_only_unverifiable_factors_logs_in_to_set_up_a_factor(
    respx_zitadel: respx.MockRouter,
) -> None:
    """Passkey and security-key users are routed to set-up, as a user without any factor is."""
    _mock_user(respx_zitadel, ["AUTHENTICATION_METHOD_TYPE_PASSKEY", "AUTHENTICATION_METHOD_TYPE_U2F"])
    respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": _CALLBACK})
    )
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        result = await login(body=_make_login_body(), response=MagicMock(), request=make_request(), db=_make_db_mock())

    assert result.status == "ok"
    assert [e["reason"] for e in captured if e.get("event") == "mfa_setup_required"] == ["no_mfa_enrolled"]


@pytest.mark.asyncio
async def test_optional_policy_answers_503_when_factors_cannot_be_read(respx_zitadel: respx.MockRouter) -> None:
    _mock_user(respx_zitadel, status=500)
    finalize = respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": _CALLBACK})
    )
    audit_patch, emit_patch = _audit_emit_patches()

    with capture_logs() as captured, audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await login(
                body=_make_login_body(),
                response=MagicMock(),
                request=make_request(),
                db=_make_db_mock(org_mfa_policy="optional"),
            )

    assert exc.value.status_code == 503
    assert finalize.call_count == 0
    events = [e for e in captured if e.get("event") == "mfa_check_failed"]
    assert [(e["mfa_policy"], e["outcome"]) for e in events] == [("optional", "503")]


@pytest.mark.asyncio
async def test_email_code_that_cannot_be_sent_answers_503(respx_zitadel: respx.MockRouter, fake_redis: Any) -> None:
    _mock_user(respx_zitadel, [_OTP_EMAIL])
    respx_zitadel.patch("/v2/sessions/sess-abc").mock(return_value=httpx.Response(500, json={}))
    finalize = respx_zitadel.post(url__regex=r"/v2/oidc/auth_requests/.+").mock(
        return_value=httpx.Response(200, json={"callbackUrl": _CALLBACK})
    )
    audit_patch, emit_patch = _audit_emit_patches()

    with audit_patch, emit_patch:
        with pytest.raises(HTTPException) as exc:
            await login(body=_make_login_body(), response=MagicMock(), request=make_request(), db=_make_db_mock())

    assert exc.value.status_code == 503
    assert finalize.call_count == 0


@pytest.mark.asyncio
async def test_me_counts_only_factors_the_login_verifies(respx_zitadel: respx.MockRouter) -> None:
    from test_me_org_found import _mock_org, _mock_perms, _mock_portal_user

    from app.api.me import me
    from app.services.zitadel import zitadel as zitadel_singleton

    org = _mock_org()
    org.mfa_policy = "required"
    row = MagicMock()
    row.__iter__ = lambda self: iter((org, _mock_portal_user()))
    result = MagicMock()
    result.one_or_none.return_value = row
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    credentials = MagicMock(credentials="test-token")
    respx_zitadel.get("/v2/users/user-123/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": ["AUTHENTICATION_METHOD_TYPE_U2F"]})
    )
    userinfo = {"sub": "user-123", "email": "test@acme.example.com", "name": "Test User"}

    with (
        patch.object(zitadel_singleton, "get_userinfo", AsyncMock(return_value=userinfo)),
        patch("app.api.me.resolve_user_permissions", new=AsyncMock(return_value=_mock_perms(org_id=org.id))),
        patch("app.api.me.set_tenant", new_callable=AsyncMock),
    ):
        response = await me(credentials=credentials, db=db)

    assert response.mfa_enrolled is False
    assert response.requires_2fa_setup is True
