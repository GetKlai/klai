"""Tests for the social signup flow (SPEC-AUTH-001).

Covers:
- POST /api/auth/idp-intent-signup   → starts IDP flow, returns auth_url
- GET  /api/auth/idp-signup-callback → new user path + existing user path
- POST /api/signup/social            → completes signup, creates org + user

All external calls (Zitadel, DB, provisioning) are mocked.
All string values below are test placeholders, NOT real credentials.
"""

# ruff: noqa: S106  -- literal strings below are test placeholders, not real credentials

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

_FERNET_KEY = "R1c1-s96uO9Yz7k1E0kN6qz52gzd9PwNbAeZaks_PIc="  # nosec — test placeholder
_GOOGLE_IDP_ID = "368810756424073247"
_PORTAL_URL = "https://portal.getklai.com"
_DOMAIN = "getklai.com"

_FAKE_SESSION_ID = "session-abc-123"
_FAKE_SESSION_TOKEN = "token-xyz-456"  # nosec — test placeholder
_FAKE_USER_ID = "zitadel-user-001"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://auth.test/zitadel")
    response = httpx.Response(status_code, request=request, text="err")
    return httpx.HTTPStatusError("err", request=request, response=response)


def _encrypt_pending(session_id: str, session_token: str, user_id: str) -> str:
    """Return a valid Fernet-encrypted klai_idp_pending cookie value."""
    fernet = Fernet(_FERNET_KEY.encode())
    payload = json.dumps(
        {"session_id": session_id, "session_token": session_token, "zitadel_user_id": user_id}
    ).encode()
    return fernet.encrypt(payload).decode()


def _session_detail(
    user_id: str, first_name: str = "Jan", last_name: str = "Jansen", email: str = "jan@example.com"
) -> dict:
    """Build a fake Zitadel get_session response."""
    return {
        "session": {
            "factors": {
                "user": {"id": user_id, "displayName": f"{first_name} {last_name}", "loginName": email},
                "intent": {
                    "idpInformation": {
                        "rawInformation": {"given_name": first_name, "family_name": last_name, "email": email}
                    }
                },
            }
        }
    }


# ---------------------------------------------------------------------------
# POST /api/auth/idp-intent-signup
# ---------------------------------------------------------------------------


class TestIDPIntentSignup:
    """POST /api/auth/idp-intent-signup starts the IDP flow and returns auth_url."""

    @pytest.mark.asyncio
    async def test_returns_auth_url(self) -> None:
        from app.api.auth import IDPIntentSignupRequest, idp_intent_signup

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            mock_settings.zitadel_idp_google_id = _GOOGLE_IDP_ID
            mock_settings.zitadel_idp_microsoft_id = "microsoft-idp-id"
            mock_settings.portal_url = _PORTAL_URL
            mock_zitadel.create_idp_intent = AsyncMock(
                return_value={"authUrl": "https://accounts.google.com/o/oauth2/auth?..."}
            )

            result = await idp_intent_signup(IDPIntentSignupRequest(idp_id=_GOOGLE_IDP_ID))

            assert result.auth_url.startswith("https://accounts.google.com/")

    @pytest.mark.asyncio
    async def test_locale_embedded_in_success_url(self) -> None:
        """The locale is passed through to the callback via success_url query param."""
        from app.api.auth import IDPIntentSignupRequest, idp_intent_signup

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            mock_settings.zitadel_idp_google_id = _GOOGLE_IDP_ID
            mock_settings.zitadel_idp_microsoft_id = "microsoft-idp-id"
            mock_settings.portal_url = _PORTAL_URL
            mock_zitadel.create_idp_intent = AsyncMock(return_value={"authUrl": "https://auth.example.com/"})

            await idp_intent_signup(IDPIntentSignupRequest(idp_id=_GOOGLE_IDP_ID, locale="en"))

            call_args = mock_zitadel.create_idp_intent.call_args
            success_url: str = call_args.args[1] if call_args.args else call_args.kwargs["success_url"]
            assert "locale=en" in success_url

    @pytest.mark.asyncio
    async def test_invalid_locale_defaults_to_nl(self) -> None:
        from app.api.auth import IDPIntentSignupRequest, idp_intent_signup

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            mock_settings.zitadel_idp_google_id = _GOOGLE_IDP_ID
            mock_settings.zitadel_idp_microsoft_id = "microsoft-idp-id"
            mock_settings.portal_url = _PORTAL_URL
            mock_zitadel.create_idp_intent = AsyncMock(return_value={"authUrl": "https://auth.example.com/"})

            # "fr" is not supported → should default to "nl"
            await idp_intent_signup(IDPIntentSignupRequest(idp_id=_GOOGLE_IDP_ID, locale="fr"))

            call_args = mock_zitadel.create_idp_intent.call_args
            success_url: str = call_args.args[1] if call_args.args else call_args.kwargs["success_url"]
            assert "locale=nl" in success_url

    @pytest.mark.asyncio
    async def test_unknown_idp_raises_400(self) -> None:
        from app.api.auth import IDPIntentSignupRequest, idp_intent_signup

        with patch("app.api.auth.settings") as mock_settings:
            mock_settings.zitadel_idp_google_id = _GOOGLE_IDP_ID
            mock_settings.zitadel_idp_microsoft_id = "microsoft-idp-id"

            with pytest.raises(HTTPException) as exc_info:
                await idp_intent_signup(IDPIntentSignupRequest(idp_id="unknown-idp"))

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_zitadel_error_raises_502(self) -> None:
        from app.api.auth import IDPIntentSignupRequest, idp_intent_signup

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            mock_settings.zitadel_idp_google_id = _GOOGLE_IDP_ID
            mock_settings.zitadel_idp_microsoft_id = "microsoft-idp-id"
            mock_settings.portal_url = _PORTAL_URL
            mock_zitadel.create_idp_intent = AsyncMock(side_effect=_make_http_error(500))

            with pytest.raises(HTTPException) as exc_info:
                await idp_intent_signup(IDPIntentSignupRequest(idp_id=_GOOGLE_IDP_ID))

            assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# GET /api/auth/idp-signup-callback
# ---------------------------------------------------------------------------


def _configure_zitadel_mock(
    mock_zitadel: MagicMock,
    *,
    intent_user_id: str | None = _FAKE_USER_ID,
    retrieve_idp_intent_error: Exception | None = None,
    create_user_return: str = _FAKE_USER_ID,
    create_user_error: Exception | None = None,
    session_return: dict | None = None,
    create_session_error: Exception | None = None,
    session_detail: dict | None = None,
    get_session_error: Exception | None = None,
) -> None:
    """Wire up AsyncMocks on the patched ``zitadel`` module for ``idp_signup_callback``.

    The production flow calls, in order:
      1. ``retrieve_idp_intent(id, token)`` → dict (optionally with ``userId``)
      2. ``create_zitadel_user_from_idp(intent_data, org_id)`` → str  [only if no userId]
      3. ``create_session_for_user_idp(user_id, id, token)`` → dict (sessionId, sessionToken)
      4. ``get_session(session_id, session_token)`` → dict (nested session.factors)

    Every test configures defaults up-front; only the fields relevant to the
    scenario are overridden via keyword args.
    """
    intent_data = {"userId": intent_user_id} if intent_user_id else {}
    mock_zitadel.retrieve_idp_intent = AsyncMock(
        side_effect=retrieve_idp_intent_error,
        return_value=intent_data if retrieve_idp_intent_error is None else None,
    )
    mock_zitadel.create_zitadel_user_from_idp = AsyncMock(
        side_effect=create_user_error,
        return_value=create_user_return if create_user_error is None else None,
    )
    default_session = {"sessionId": _FAKE_SESSION_ID, "sessionToken": _FAKE_SESSION_TOKEN}
    mock_zitadel.create_session_for_user_idp = AsyncMock(
        side_effect=create_session_error,
        return_value=session_return if session_return is not None else default_session,
    )
    mock_zitadel.get_session = AsyncMock(
        side_effect=get_session_error,
        return_value=session_detail if session_detail is not None else _session_detail(_FAKE_USER_ID),
    )


def _configure_settings_mock(mock_settings: MagicMock) -> None:
    """Provide the settings fields read by ``idp_signup_callback``."""
    mock_settings.portal_url = _PORTAL_URL
    mock_settings.domain = _DOMAIN
    mock_settings.sso_cookie_max_age = 3600
    mock_settings.zitadel_portal_org_id = "362757920133283846"


class TestIDPSignupCallback:
    """GET /api/auth/idp-signup-callback branches on new vs existing Zitadel user.

    The endpoint chains four Zitadel API calls and one DB lookup. Helpers
    ``_configure_zitadel_mock`` and ``_configure_settings_mock`` wire up
    happy-path defaults; each test overrides only the field it is exercising.
    """

    @pytest.mark.asyncio
    async def test_new_user_sets_pending_cookie(self) -> None:
        """Happy path — new user: encrypted pending cookie + redirect to /signup/social."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)  # no existing PortalUser

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth._fernet") as mock_fernet,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel)
            mock_fernet.encrypt = MagicMock(return_value=b"ENCRYPTED_PENDING")

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        assert response.status_code == 302
        location = response.headers["location"]
        assert "/nl/signup/social" in location
        assert "first_name=" in location
        assert "klai_idp_pending" in response.headers.get("set-cookie", "")

    @pytest.mark.asyncio
    async def test_new_user_locale_en_in_redirect(self) -> None:
        """Locale=en is preserved in the redirect to /signup/social."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth._fernet") as mock_fernet,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel)
            mock_fernet.encrypt = MagicMock(return_value=b"ENCRYPTED_PENDING")

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="en", db=db)

        assert "/en/signup/social" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_existing_user_sets_sso_cookie(self) -> None:
        """Existing user path: SSO cookie set, redirect to portal root."""
        from app.api.auth import idp_signup_callback

        existing_user = MagicMock()
        db = AsyncMock()
        db.scalar = AsyncMock(return_value=existing_user)

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth._fernet") as mock_fernet,
            patch("app.api.auth.emit_event") as mock_emit_event,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel)
            mock_fernet.encrypt = MagicMock(return_value=b"ENCRYPTED_SSO")

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        assert response.status_code == 302
        assert response.headers["location"] == f"{_PORTAL_URL}/"
        assert "klai_sso" in response.headers.get("set-cookie", "")
        # Existing-user branch must record the login event
        mock_emit_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_new_zitadel_user_created_when_intent_lacks_user_id(self) -> None:
        """When retrieve_idp_intent returns no userId, create_zitadel_user_from_idp is called."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth._fernet") as mock_fernet,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(
                mock_zitadel,
                intent_user_id=None,  # forces create_zitadel_user_from_idp branch
                create_user_return="new-zitadel-user-999",
            )
            mock_fernet.encrypt = MagicMock(return_value=b"ENCRYPTED_PENDING")

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        mock_zitadel.create_zitadel_user_from_idp.assert_awaited_once()
        mock_zitadel.create_session_for_user_idp.assert_awaited()
        # user_id arg to create_session_for_user_idp must be the one from create_zitadel_user_from_idp
        call_args = mock_zitadel.create_session_for_user_idp.await_args
        assert call_args.args[0] == "new-zitadel-user-999"
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_retrieve_intent_failure_redirects_to_failure_url(self) -> None:
        """HTTPStatusError on retrieve_idp_intent short-circuits to failure redirect."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel, retrieve_idp_intent_error=_make_http_error(500))

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        assert response.status_code == 302
        assert "error=idp_failed" in response.headers["location"]
        mock_zitadel.create_session_for_user_idp.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_zitadel_user_failure_redirects_to_failure_url(self) -> None:
        """HTTPStatusError on create_zitadel_user_from_idp redirects to failure_url."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(
                mock_zitadel,
                intent_user_id=None,
                create_user_error=_make_http_error(500),
            )

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        assert response.status_code == 302
        assert "error=idp_failed" in response.headers["location"]
        mock_zitadel.create_session_for_user_idp.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_session_creation_failure_redirects_to_failure_url(self) -> None:
        """Non-404 HTTPStatusError on create_session_for_user_idp → failure (no retry)."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel, create_session_error=_make_http_error(400))

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        assert response.status_code == 302
        assert "/nl/signup" in response.headers["location"]
        assert "error=idp_failed" in response.headers["location"]
        # 400 is not a retryable status — exactly one attempt
        assert mock_zitadel.create_session_for_user_idp.await_count == 1

    @pytest.mark.asyncio
    async def test_session_creation_retries_on_404(self) -> None:
        """Zitadel CQRS lag can yield 404 on create_session — production code retries up to 4 times."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)
        session_success = {"sessionId": _FAKE_SESSION_ID, "sessionToken": _FAKE_SESSION_TOKEN}

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth._fernet") as mock_fernet,
            patch("app.api.auth.asyncio.sleep", new_callable=AsyncMock),
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel)
            # First two attempts 404, third attempt succeeds
            mock_zitadel.create_session_for_user_idp = AsyncMock(
                side_effect=[
                    _make_http_error(404),
                    _make_http_error(404),
                    session_success,
                ]
            )
            mock_fernet.encrypt = MagicMock(return_value=b"ENCRYPTED_PENDING")

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        assert response.status_code == 302
        assert mock_zitadel.create_session_for_user_idp.await_count == 3
        assert "klai_idp_pending" in response.headers.get("set-cookie", "")

    @pytest.mark.asyncio
    async def test_session_missing_ids_redirects_to_failure_url(self) -> None:
        """create_session_for_user_idp returning empty sessionId/sessionToken → failure."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel, session_return={})  # empty dict, no ids

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        assert response.status_code == 302
        assert "error=idp_failed" in response.headers["location"]
        mock_zitadel.get_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_session_failure_redirects_to_failure_url(self) -> None:
        """HTTPStatusError on get_session → failure redirect."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel, get_session_error=_make_http_error(500))

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        assert response.status_code == 302
        assert "error=idp_failed" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_missing_user_id_redirects_to_failure_url(self) -> None:
        """get_session returning session without factors.user.id → failure."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel, session_detail={"session": {"factors": {}}})

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="nl", db=db)

        assert response.status_code == 302
        assert "error=idp_failed" in response.headers["location"]

    @pytest.mark.asyncio
    async def test_unknown_locale_defaults_to_nl(self) -> None:
        """Unsupported locale falls back to 'nl' in the redirect."""
        from app.api.auth import idp_signup_callback

        db = AsyncMock()

        with (
            patch("app.api.auth.settings") as mock_settings,
            patch("app.api.auth.zitadel") as mock_zitadel,
        ):
            _configure_settings_mock(mock_settings)
            _configure_zitadel_mock(mock_zitadel, retrieve_idp_intent_error=_make_http_error(500))

            response = await idp_signup_callback(id="intent-id", token="intent-token", locale="de", db=db)

        # Even when locale="de", failure_url uses "nl"
        assert "/nl/signup" in response.headers["location"]


# ---------------------------------------------------------------------------
# POST /api/signup/social
# ---------------------------------------------------------------------------


class TestSignupSocial:
    """POST /api/signup/social completes the social signup after company name entry."""

    def _make_body(self, company_name: str = "Klai BV"):
        from app.api.signup import SocialSignupRequest

        return SocialSignupRequest(company_name=company_name)

    def _make_response_mock(self):
        response = MagicMock()
        response.set_cookie = MagicMock()
        response.delete_cookie = MagicMock()
        return response

    @pytest.mark.asyncio
    async def test_happy_path_returns_redirect_url(self) -> None:
        from app.api.signup import signup_social

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()

        org_row = MagicMock()
        org_row.id = 99
        org_row.slug = "klai-bv"
        org_row.plan = "free"

        pending_cookie = _encrypt_pending(_FAKE_SESSION_ID, _FAKE_SESSION_TOKEN, _FAKE_USER_ID)

        with (
            patch("app.api.signup.settings") as mock_settings,
            patch("app.api.signup.zitadel") as mock_zitadel,
            patch("app.api.signup.PortalOrg") as mock_portal_org,
            patch("app.api.signup.PortalUser"),
            patch("app.api.signup.provision_tenant"),
            patch("app.api.signup.emit_event"),
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
        ):
            mock_settings.zitadel_portal_org_id = "portal-org-id"
            mock_settings.domain = _DOMAIN
            mock_settings.sso_cookie_max_age = 3600
            mock_settings.sso_cookie_key = _FERNET_KEY
            mock_zitadel.create_org = AsyncMock(return_value={"id": "zit-org-new"})
            mock_zitadel.grant_user_role = AsyncMock()
            mock_portal_org.return_value = org_row

            response_mock = self._make_response_mock()
            background_tasks = MagicMock()

            result = await signup_social(
                body=self._make_body(),
                response=response_mock,
                background_tasks=background_tasks,
                db=db,
                klai_idp_pending=pending_cookie,
            )

        assert result.redirect_url == "/"
        assert result.org_id == "zit-org-new"
        assert result.user_id == _FAKE_USER_ID
        response_mock.set_cookie.assert_called_once()
        response_mock.delete_cookie.assert_called_once()
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_missing_cookie_raises_400(self) -> None:
        from app.api.signup import signup_social

        db = AsyncMock()
        response_mock = self._make_response_mock()

        with pytest.raises(HTTPException) as exc_info:
            await signup_social(
                body=self._make_body(),
                response=response_mock,
                background_tasks=MagicMock(),
                db=db,
                klai_idp_pending=None,
            )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_cookie_raises_400(self) -> None:
        from app.api.signup import signup_social

        db = AsyncMock()
        response_mock = self._make_response_mock()

        with patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())):
            with pytest.raises(HTTPException) as exc_info:
                await signup_social(
                    body=self._make_body(),
                    response=response_mock,
                    background_tasks=MagicMock(),
                    db=db,
                    klai_idp_pending="this-is-not-a-valid-fernet-token",
                )

        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_company_name_conflict_raises_409(self) -> None:
        from app.api.signup import signup_social

        db = AsyncMock()
        pending_cookie = _encrypt_pending(_FAKE_SESSION_ID, _FAKE_SESSION_TOKEN, _FAKE_USER_ID)

        with (
            patch("app.api.signup.settings") as mock_settings,
            patch("app.api.signup.zitadel") as mock_zitadel,
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
        ):
            mock_settings.zitadel_portal_org_id = "portal-org-id"
            mock_zitadel.create_org = AsyncMock(side_effect=_make_http_error(409))

            with pytest.raises(HTTPException) as exc_info:
                await signup_social(
                    body=self._make_body(),
                    response=self._make_response_mock(),
                    background_tasks=MagicMock(),
                    db=db,
                    klai_idp_pending=pending_cookie,
                )

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_zitadel_org_failure_raises_502(self) -> None:
        from app.api.signup import signup_social

        db = AsyncMock()
        pending_cookie = _encrypt_pending(_FAKE_SESSION_ID, _FAKE_SESSION_TOKEN, _FAKE_USER_ID)

        with (
            patch("app.api.signup.settings") as mock_settings,
            patch("app.api.signup.zitadel") as mock_zitadel,
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
        ):
            mock_settings.zitadel_portal_org_id = "portal-org-id"
            mock_zitadel.create_org = AsyncMock(side_effect=_make_http_error(500))

            with pytest.raises(HTTPException) as exc_info:
                await signup_social(
                    body=self._make_body(),
                    response=self._make_response_mock(),
                    background_tasks=MagicMock(),
                    db=db,
                    klai_idp_pending=pending_cookie,
                )

        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_role_grant_failure_raises_502(self) -> None:
        from app.api.signup import signup_social

        db = AsyncMock()
        pending_cookie = _encrypt_pending(_FAKE_SESSION_ID, _FAKE_SESSION_TOKEN, _FAKE_USER_ID)

        with (
            patch("app.api.signup.settings") as mock_settings,
            patch("app.api.signup.zitadel") as mock_zitadel,
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
        ):
            mock_settings.zitadel_portal_org_id = "portal-org-id"
            mock_zitadel.create_org = AsyncMock(return_value={"id": "zit-org-new"})
            mock_zitadel.grant_user_role = AsyncMock(side_effect=Exception("Zitadel unreachable"))

            with pytest.raises(HTTPException) as exc_info:
                await signup_social(
                    body=self._make_body(),
                    response=self._make_response_mock(),
                    background_tasks=MagicMock(),
                    db=db,
                    klai_idp_pending=pending_cookie,
                )

        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_db_commit_failure_raises_502(self) -> None:
        from app.api.signup import signup_social

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock(side_effect=Exception("DB timeout"))
        db.rollback = AsyncMock()

        pending_cookie = _encrypt_pending(_FAKE_SESSION_ID, _FAKE_SESSION_TOKEN, _FAKE_USER_ID)

        org_row = MagicMock()
        org_row.id = 99
        org_row.slug = "klai-bv"

        with (
            patch("app.api.signup.settings") as mock_settings,
            patch("app.api.signup.zitadel") as mock_zitadel,
            patch("app.api.signup.PortalOrg") as mock_portal_org,
            patch("app.api.signup.PortalUser"),
            patch("app.api.signup._get_fernet", return_value=Fernet(_FERNET_KEY.encode())),
        ):
            mock_settings.zitadel_portal_org_id = "portal-org-id"
            mock_zitadel.create_org = AsyncMock(return_value={"id": "zit-org-new"})
            mock_zitadel.grant_user_role = AsyncMock()
            mock_portal_org.return_value = org_row

            with pytest.raises(HTTPException) as exc_info:
                await signup_social(
                    body=self._make_body(),
                    response=self._make_response_mock(),
                    background_tasks=MagicMock(),
                    db=db,
                    klai_idp_pending=pending_cookie,
                )

        assert exc_info.value.status_code == 502
        db.rollback.assert_awaited()
