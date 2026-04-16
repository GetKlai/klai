"""Tests for OAuth provider routes (SPEC-KB-025).

Covers:
- GET /api/oauth/providers  -> capability advertisement driven by settings
- GET /api/oauth/{provider}/authorize  -> redirect to provider consent page + state cookie
- GET /api/oauth/{provider}/callback  -> state validation, code exchange, writeback
- PATCH /internal/connectors/{connector_id}/credentials  -> encrypted writeback path

Unit tests: all external calls (Zitadel, httpx, credential_store) are mocked.
All string values below are test placeholders, NOT real credentials.
"""

# ruff: noqa: S106  -- literal strings below are test placeholders, not real credentials

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

# Test-only placeholder literals (NOT credentials)
_PLACEHOLDER_COOKIE_KEY = "R1c1-s96uO9Yz7k1E0kN6qz52gzd9PwNbAeZaks_PIc="
_PLACEHOLDER_INTERNAL = "placeholder-internal-value"  # nosec
_PLACEHOLDER_CLIENT_ID = "placeholder-client-id"  # nosec
_PLACEHOLDER_CLIENT_SECRET = "placeholder-client-secret"  # nosec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http_response(status_code: int, json_body: dict | None = None) -> MagicMock:
    """Build a mock httpx.Response-ish object with .status_code, .json(), .raise_for_status()."""
    response = MagicMock()
    response.status_code = status_code
    response.json = MagicMock(return_value=json_body or {})
    if status_code >= 400:
        request = httpx.Request("POST", "https://oauth.test/token")
        err_response = httpx.Response(status_code, request=request, text="err")
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("err", request=request, response=err_response)
        )
    else:
        response.raise_for_status = MagicMock(return_value=None)
    return response


def _mock_httpx_client(post_response: MagicMock) -> MagicMock:
    """Return an httpx.AsyncClient class replacement whose post() returns post_response."""
    client = MagicMock()
    client.post = AsyncMock(return_value=post_response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    cls = MagicMock(return_value=client)
    return cls


# ---------------------------------------------------------------------------
# GET /api/oauth/providers
# ---------------------------------------------------------------------------


class TestProvidersEndpoint:
    """GET /api/oauth/providers returns an enabled map derived from settings."""

    @pytest.mark.asyncio
    async def test_providers_endpoint_reflects_enabled_google_drive(self) -> None:
        """google_drive_client_id set -> google_drive enabled=True."""
        from app.api.oauth import list_providers

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.google_drive_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.sharepoint_client_id = ""

            result = await list_providers(user_id="zitadel-user-1")

            assert result["google_drive"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_providers_endpoint_reflects_disabled_google_drive(self) -> None:
        """google_drive_client_id empty -> google_drive enabled=False."""
        from app.api.oauth import list_providers

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = ""
            mock_settings.google_drive_client_secret = ""
            mock_settings.sharepoint_client_id = ""

            result = await list_providers(user_id="zitadel-user-1")

            assert result["google_drive"]["enabled"] is False


# ---------------------------------------------------------------------------
# GET /api/oauth/{provider}/authorize
# ---------------------------------------------------------------------------


class TestAuthorizeEndpoint:
    """GET /api/oauth/google_drive/authorize redirects to Google and sets state cookie."""

    @pytest.mark.asyncio
    async def test_authorize_returns_authorize_url(self) -> None:
        """Returns 200 JSON with authorize_url pointing to accounts.google.com."""
        import json

        from app.api.oauth import authorize_provider

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.google_drive_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.frontend_url = "https://portal.getklai.com"
            mock_settings.domain = "getklai.com"

            response = await authorize_provider(
                provider="google_drive",
                kb_slug="main",
                user_id="zitadel-user-1",
            )

            assert response.status_code == 200
            body = json.loads(response.body)
            assert body["authorize_url"].startswith("https://accounts.google.com/")

    @pytest.mark.asyncio
    async def test_authorize_sets_state_cookie(self) -> None:
        """Redirect response must set a signed klai_oauth_state cookie."""
        from app.api.oauth import authorize_provider

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.google_drive_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.frontend_url = "https://portal.getklai.com"
            mock_settings.domain = "getklai.com"

            response = await authorize_provider(
                provider="google_drive",
                kb_slug="main",
                user_id="zitadel-user-1",
            )

            set_cookie = response.headers.get("set-cookie", "")
            assert "klai_oauth_state" in set_cookie

    @pytest.mark.asyncio
    async def test_authorize_rejects_disabled_provider(self) -> None:
        """Empty google_drive_client_id -> 404 Not Found."""
        from app.api.oauth import authorize_provider

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = ""
            mock_settings.google_drive_client_secret = ""
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY

            with pytest.raises(HTTPException) as exc_info:
                await authorize_provider(
                    provider="google_drive",
                    kb_slug="main",
                    user_id="zitadel-user-1",
                )

            assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/oauth/{provider}/callback
# ---------------------------------------------------------------------------


class TestCallbackEndpoint:
    """GET /api/oauth/google_drive/callback validates state, exchanges code, stores tokens."""

    @pytest.mark.asyncio
    async def test_callback_rejects_tampered_state(self) -> None:
        """Invalid / forged state cookie -> 400 Bad Request."""
        from app.api.oauth import callback_provider

        db = AsyncMock()

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.google_drive_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.frontend_url = "https://portal.getklai.com"

            with pytest.raises(HTTPException) as exc_info:
                await callback_provider(
                    provider="google_drive",
                    code="auth-code-xyz",
                    state="tampered-state-value",
                    klai_oauth_state="totally-different-value",
                    user_id="zitadel-user-1",
                    db=db,
                )

            assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_callback_exchanges_code_for_tokens(self) -> None:
        """Valid state + code -> token exchange + connector.encrypted_credentials populated."""
        from app.api.oauth import _sign_state, callback_provider

        db = AsyncMock()

        mock_connector = MagicMock()
        mock_connector.id = "conn-uuid-1"
        mock_connector.org_id = 42
        mock_connector.connector_type = "google_drive"
        mock_connector.config = {}
        mock_connector.encrypted_credentials = None

        mock_portal_user = MagicMock()
        mock_portal_user.org_id = 42

        # Provider returns opaque placeholder values (NOT real credentials)
        token_response = _make_http_response(
            200,
            {
                "access_token": "placeholder-access-value",
                "refresh_token": "placeholder-refresh-value",
                "expires_in": 3599,
                "token_type": "Bearer",
                "scope": "https://www.googleapis.com/auth/drive.readonly",
            },
        )

        with (
            patch("app.api.oauth.settings") as mock_settings,
            patch("app.api.oauth.httpx.AsyncClient", _mock_httpx_client(token_response)),
            patch("app.api.oauth.credential_store") as mock_store,
            patch("app.api.oauth.select"),
        ):
            mock_settings.google_drive_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.google_drive_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.frontend_url = "https://portal.getklai.com"
            mock_settings.domain = "getklai.com"

            state_token = _sign_state(
                {"connector_id": "conn-uuid-1", "user_id": "zitadel-user-1", "provider": "google_drive"}
            )

            db.scalar = AsyncMock(return_value=mock_portal_user)
            db.get = AsyncMock(return_value=mock_connector)
            db.commit = AsyncMock()

            mock_store.encrypt_credentials = AsyncMock(
                return_value=(b"ENCRYPTED_BLOB", {"access_token": "***", "refresh_token": "***"})
            )

            response = await callback_provider(
                provider="google_drive",
                code="auth-code-xyz",
                state=state_token,
                klai_oauth_state=state_token,
                user_id="zitadel-user-1",
                db=db,
            )

            assert response.status_code in (302, 303, 307)
            mock_store.encrypt_credentials.assert_called_once()
            db.commit.assert_awaited()
            assert mock_connector.encrypted_credentials == b"ENCRYPTED_BLOB"


# ---------------------------------------------------------------------------
# PATCH /internal/connectors/{connector_id}/credentials
# ---------------------------------------------------------------------------


class TestInternalCredentialsWriteback:
    """PATCH /internal/connectors/{connector_id}/credentials re-encrypts refreshed tokens."""

    @pytest.mark.asyncio
    async def test_writeback_updates_access_token(self) -> None:
        """Refreshed access_token flows through credential_store and is persisted."""
        from app.api.internal import CredentialsUpdate, update_connector_credentials

        db = AsyncMock()

        mock_connector = MagicMock()
        mock_connector.id = "conn-uuid-2"
        mock_connector.org_id = 77
        mock_connector.connector_type = "google_drive"
        mock_connector.config = {}
        mock_connector.encrypted_credentials = b"PREVIOUSLY_ENCRYPTED"

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {_PLACEHOLDER_INTERNAL}"}

        body = CredentialsUpdate(
            access_token="placeholder-new-access-value",
            token_expiry="2026-04-16T12:00:00+00:00",
        )

        with (
            patch("app.api.internal.settings") as mock_settings,
            patch("app.api.internal.credential_store") as mock_store,
        ):
            mock_settings.internal_secret = _PLACEHOLDER_INTERNAL

            db.get = AsyncMock(return_value=mock_connector)
            db.commit = AsyncMock()

            mock_store.decrypt_credentials = AsyncMock(
                return_value={
                    "access_token": "placeholder-old-access-value",
                    "refresh_token": "placeholder-refresh-value",
                }
            )
            mock_store.encrypt_credentials = AsyncMock(
                return_value=(b"NEW_ENCRYPTED", {"access_token": "***", "refresh_token": "***"})
            )

            await update_connector_credentials(
                connector_id="conn-uuid-2",
                body=body,
                request=request,
                db=db,
            )

            mock_store.encrypt_credentials.assert_called_once()
            # Extract the merged-config argument regardless of positional/keyword call style
            call = mock_store.encrypt_credentials.call_args
            merged = call.kwargs.get("config")
            if merged is None and len(call.args) >= 3:
                merged = call.args[2]
            assert merged is not None
            assert merged["access_token"] == "placeholder-new-access-value"
            assert merged["refresh_token"] == "placeholder-refresh-value"
            assert merged["token_expiry"] == "2026-04-16T12:00:00+00:00"

            assert mock_connector.encrypted_credentials == b"NEW_ENCRYPTED"
            db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_writeback_rejects_missing_internal_secret(self) -> None:
        """Wrong / missing Authorization header -> 401 Unauthorized."""
        from app.api.internal import CredentialsUpdate, update_connector_credentials

        db = AsyncMock()

        request = MagicMock()
        request.headers = {"Authorization": "Bearer WRONG"}

        body = CredentialsUpdate(access_token="placeholder-value")

        with patch("app.api.internal.settings") as mock_settings:
            mock_settings.internal_secret = _PLACEHOLDER_INTERNAL

            with pytest.raises(HTTPException) as exc_info:
                await update_connector_credentials(
                    connector_id="conn-uuid-2",
                    body=body,
                    request=request,
                    db=db,
                )

            assert exc_info.value.status_code == 401
