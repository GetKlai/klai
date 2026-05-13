"""SPEC-PORTAL-AUTH-AUTOLOGIN-001 — happy-path + fallback for auto-login after password-set.

The handler chains:
  1. zitadel.set_password_with_code             (POST /v2/users/{id}/password)
  2. zitadel.has_any_mfa                        (GET  /v2/users/{id}/authentication_methods)
  3. _initiate_server_side_authorize            (GET  /oauth/v2/authorize, follow_redirects=False)
  4. zitadel.create_session_with_password       (POST /v2/sessions)
  5. zitadel.finalize_auth_request              (POST /v2/oidc/auth_requests/{id})
  6. exchange_code_for_tokens                   (POST /oauth/v2/token)
  7. session_service.create + set_session_cookies

Any failure in 2-7 falls back to ``{redirect_to: '/', auto_login_failed: true}``
with the password-set itself remaining successful.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from auth_test_helpers import _audit_log_patch
from starlette.requests import Request


def _mock_request() -> Request:
    return Request(
        scope={
            "type": "http",
            "method": "POST",
            "path": "/api/auth/password/set",
            "headers": [(b"user-agent", b"pytest-autologin")],
            "client": ("127.0.0.1", 12345),
            "query_string": b"",
        }
    )


# ---------------------------------------------------------------------------
# Helper: build mocked respx routes for the full happy-path chain
# ---------------------------------------------------------------------------


def _wire_full_chain(
    router: respx.MockRouter,
    *,
    user_id: str = "uid-1",
    auth_request_id: str = "V2_xyz",
    has_mfa: bool = False,
) -> None:
    # 1a. set_password_with_code tries invite_code/verify first → 200
    router.post(url__regex=rf"/v2/users/{user_id}/invite_code/verify").mock(
        return_value=httpx.Response(200, json={"details": {"sequence": "11"}}),
    )
    # 1b. then password (without verificationCode) → 200
    router.post(url__regex=rf"/v2/users/{user_id}/password$").mock(return_value=httpx.Response(200, json={}))
    # 2. has_any_mfa → list with one method (true) or empty (false)
    methods = [{"type": "AUTHENTICATION_METHOD_TYPE_TOTP"}] if has_mfa else []
    router.get(url__regex=rf"/v2/users/{user_id}/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": methods}),
    )
    # 3. /oauth/v2/authorize → 302 with Location: …login?authRequest=V2_xyz
    router.get(url__regex=r".*/oauth/v2/authorize.*").mock(
        return_value=httpx.Response(
            302,
            headers={"location": f"https://my.test/login?authRequest={auth_request_id}"},
        ),
    )
    # 4. create_session_with_password → sessionId + sessionToken
    router.post(url__regex=r".*/v2/sessions$").mock(
        return_value=httpx.Response(200, json={"sessionId": "sess-1", "sessionToken": "tok-1"}),
    )
    # 5. finalize_auth_request → callbackUrl
    router.post(url__regex=rf".*/v2/oidc/auth_requests/{auth_request_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "callbackUrl": "https://my.test/api/auth/oidc/callback"
                "?code=AUTHCODE&state={state}"  # state placeholder filled below
            },
        ),
    )
    # 6. /oauth/v2/token → tokens
    router.post(url__regex=r".*/oauth/v2/token$").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "at-xyz",
                "refresh_token": "rt-xyz",
                "id_token": "id-xyz",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        ),
    )


# ---------------------------------------------------------------------------
# Happy-path: end-to-end auto-login success → cookies set, redirect to /setup/mfa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autologin_happy_path_sets_cookies_and_redirects_to_mfa_setup(
    respx_zitadel: respx.MockRouter, monkeypatch
) -> None:
    """Full chain succeeds: response has redirect_to=/setup/mfa + session cookie."""
    from app.api import auth as auth_module
    from app.api.auth import PasswordSetRequest, password_set

    user_id = "uid-1"
    auth_request_id = "V2_xyz"

    _wire_full_chain(respx_zitadel, user_id=user_id, auth_request_id=auth_request_id, has_mfa=False)

    # Stub the server-side authorize helper so we control the state value end-to-end.
    pending_obj = SimpleNamespace(auth_request_id=auth_request_id, code_verifier="cv-xyz", state="state-xyz")
    monkeypatch.setattr(
        "app.api.auth_bff.initiate_server_side_authorize",
        AsyncMock(return_value=pending_obj),
    )
    # finalize returns a callback URL containing our state
    monkeypatch.setattr(
        "app.services.zitadel.zitadel.finalize_auth_request",
        AsyncMock(return_value="https://my.test/api/auth/oidc/callback?code=AUTHCODE&state=state-xyz"),
    )

    # Stub oidc_pending.consume to return our seeded pending record.
    pending_record = SimpleNamespace(
        code_verifier="cv-xyz",
        return_to="/setup/mfa",
        user_agent_hash="ua-hash",
        created_at=0,
    )
    monkeypatch.setattr("app.api.auth.oidc_pending.consume", AsyncMock(return_value=pending_record))

    # Stub session_service.create — return a fake SessionRecord with sid + csrf_token.
    monkeypatch.setattr(
        "app.api.auth.session_service.create",
        AsyncMock(return_value=SimpleNamespace(sid="bff-sid-xyz", csrf_token="bff-csrf-xyz")),
    )

    # Stub the DB lookup — portal_row None is fine, org_id will be None.
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

    body = PasswordSetRequest(user_id=user_id, code="123456", new_password="NewSecret123!")

    with _audit_log_patch() as audit_log:
        response = await password_set(body=body, request=_mock_request(), db=mock_db)

    audit_log.assert_called_once()
    payload = json.loads(response.body)
    assert payload["redirect_to"] == "/setup/mfa"
    assert payload.get("auto_login_failed", False) is False

    # Session cookies must be on the response.
    cookies = [h[1].decode() for h in response.raw_headers if h[0].lower() == b"set-cookie"]
    assert any("klai_session" in c for c in cookies), f"expected session cookie; got {cookies}"
    assert any("klai_csrf" in c for c in cookies), f"expected csrf cookie; got {cookies}"

    # Sanity-check that we used the auth_module symbol (no dead-import drift).
    assert hasattr(auth_module, "password_set")


# ---------------------------------------------------------------------------
# Happy-path with MFA already enrolled → redirect to /app instead of /setup/mfa
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autologin_user_with_mfa_redirects_to_app(respx_zitadel: respx.MockRouter, monkeypatch) -> None:
    from app.api.auth import PasswordSetRequest, password_set

    user_id = "uid-mfa"
    _wire_full_chain(respx_zitadel, user_id=user_id, has_mfa=True)

    # has_any_mfa goes via the real zitadel client. The wired route returns
    # AUTHENTICATION_METHOD_TYPE_TOTP so the handler should pick /app — but
    # because we also monkeypatch initiate_server_side_authorize the actual
    # HTTP call to /authentication_methods may not fire. Stub it directly so
    # the result is unambiguous.
    monkeypatch.setattr(
        "app.services.zitadel.zitadel.has_any_mfa",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.api.auth_bff.initiate_server_side_authorize",
        AsyncMock(return_value=SimpleNamespace(auth_request_id="V2_mfa", code_verifier="cv-mfa", state="state-mfa")),
    )
    monkeypatch.setattr(
        "app.services.zitadel.zitadel.finalize_auth_request",
        AsyncMock(return_value="https://my.test/api/auth/oidc/callback?code=AC&state=state-mfa"),
    )
    monkeypatch.setattr(
        "app.api.auth.oidc_pending.consume",
        AsyncMock(
            return_value=SimpleNamespace(code_verifier="cv-mfa", return_to="/app", user_agent_hash="x", created_at=0)
        ),
    )
    monkeypatch.setattr(
        "app.api.auth.session_service.create",
        AsyncMock(return_value=SimpleNamespace(sid="s", csrf_token="c")),
    )
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

    body = PasswordSetRequest(user_id=user_id, code="123456", new_password="NewSecret123!")

    with _audit_log_patch():
        response = await password_set(body=body, request=_mock_request(), db=mock_db)

    payload = json.loads(response.body)
    assert payload["redirect_to"] == "/app"


# ---------------------------------------------------------------------------
# Fallback: any auto-login step fails → response carries auto_login_failed=true
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_autologin_fallback_when_authorize_fails(respx_zitadel: respx.MockRouter, monkeypatch) -> None:
    """If server-side /authorize doesn't return a Location, fallback fires."""
    from app.api.auth import PasswordSetRequest, password_set

    # set_password_with_code succeeds via invite-flow; everything else falls through.
    respx_zitadel.post(url__regex=r"/v2/users/uid-1/invite_code/verify").mock(
        return_value=httpx.Response(200, json={"details": {"sequence": "11"}}),
    )
    respx_zitadel.post(url__regex=r"/v2/users/uid-1/password$").mock(
        return_value=httpx.Response(200, json={}),
    )
    respx_zitadel.get(url__regex=r"/v2/users/uid-1/authentication_methods").mock(
        return_value=httpx.Response(200, json={"authMethodTypes": []}),
    )
    # /authorize returns 200 (NOT a 302) → raises OidcFlowError("authorize_unexpected_status")
    respx_zitadel.get(url__regex=r".*/oauth/v2/authorize.*").mock(
        return_value=httpx.Response(200, text="not a redirect"),
    )

    body = PasswordSetRequest(user_id="uid-1", code="123456", new_password="NewSecret123!")

    with _audit_log_patch():
        response = await password_set(body=body, request=_mock_request(), db=AsyncMock())

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert payload["redirect_to"] == "/"
    assert payload["auto_login_failed"] is True


@pytest.mark.asyncio
async def test_autologin_fallback_when_finalize_fails(respx_zitadel: respx.MockRouter, monkeypatch) -> None:
    """If finalize_auth_request raises, fallback fires; audit still emitted."""
    from app.api.auth import PasswordSetRequest, password_set

    user_id = "uid-1"
    _wire_full_chain(respx_zitadel, user_id=user_id, has_mfa=False)

    monkeypatch.setattr(
        "app.api.auth_bff.initiate_server_side_authorize",
        AsyncMock(return_value=SimpleNamespace(auth_request_id="V2_x", code_verifier="cv", state="s")),
    )
    monkeypatch.setattr(
        "app.services.zitadel.zitadel.finalize_auth_request",
        AsyncMock(side_effect=httpx.HTTPError("finalize boom")),
    )

    body = PasswordSetRequest(user_id=user_id, code="123456", new_password="NewSecret123!")

    with _audit_log_patch() as audit_log:
        response = await password_set(body=body, request=_mock_request(), db=AsyncMock())

    audit_log.assert_called_once()  # password-set audit MUST still fire
    payload = json.loads(response.body)
    assert payload["auto_login_failed"] is True
