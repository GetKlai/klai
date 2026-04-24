"""Tests for OAuth provider routes (SPEC-KB-025).

Covers:
- GET /api/oauth/providers  -> capability advertisement driven by settings
- GET /api/oauth/{provider}/authorize  -> redirect to provider consent page + state cookie
- GET /api/oauth/{provider}/callback  -> state validation, code exchange, writeback
- PATCH /internal/connectors/{connector_id}/credentials  -> encrypted writeback path

Unit tests: all external calls (Zitadel, httpx, credential_store) are mocked.
All string values below are test placeholders, NOT real credentials.
"""

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
            mock_settings.ms_docs_client_id = ""

            result = await list_providers(user_id="zitadel-user-1")

            assert result["google_drive"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_providers_endpoint_reflects_disabled_google_drive(self) -> None:
        """google_drive_client_id empty -> google_drive enabled=False."""
        from app.api.oauth import list_providers

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = ""
            mock_settings.google_drive_client_secret = ""
            mock_settings.ms_docs_client_id = ""

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
            mock_settings.portal_url = "https://portal.getklai.com"
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
            mock_settings.portal_url = "https://portal.getklai.com"
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
            mock_settings.portal_url = "https://portal.getklai.com"

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
            mock_settings.portal_url = "https://portal.getklai.com"
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
                error=None,
                error_description=None,
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
    async def test_writeback_rotates_refresh_token_when_provided(self) -> None:
        """SPEC-KB-MS-DOCS-001 R9: refresh_token in body overwrites stored RT."""
        from app.api.internal import CredentialsUpdate, update_connector_credentials

        db = AsyncMock()

        mock_connector = MagicMock()
        mock_connector.id = "conn-uuid-ms-r9"
        mock_connector.org_id = 77
        mock_connector.connector_type = "ms_docs"
        mock_connector.config = {}
        mock_connector.encrypted_credentials = b"OLD_ENCRYPTED"

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {_PLACEHOLDER_INTERNAL}"}

        body = CredentialsUpdate(
            access_token="placeholder-new-access-value",
            token_expiry="2026-04-23T12:00:00+00:00",
            refresh_token="placeholder-rotated-refresh-value",
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
                    "refresh_token": "placeholder-old-refresh-value",
                }
            )
            mock_store.encrypt_credentials = AsyncMock(
                return_value=(b"NEW_ENCRYPTED", {"access_token": "***", "refresh_token": "***"})
            )

            await update_connector_credentials(
                connector_id="conn-uuid-ms-r9",
                body=body,
                request=request,
                db=db,
            )

            call = mock_store.encrypt_credentials.call_args
            merged = call.kwargs.get("config") or (call.args[2] if len(call.args) >= 3 else None)
            assert merged is not None
            assert merged["access_token"] == "placeholder-new-access-value"
            # The NEW refresh_token replaces the stored one
            assert merged["refresh_token"] == "placeholder-rotated-refresh-value"

    @pytest.mark.asyncio
    async def test_writeback_preserves_refresh_token_when_absent(self) -> None:
        """Backward-compat: no refresh_token in body → stored RT is preserved."""
        from app.api.internal import CredentialsUpdate, update_connector_credentials

        db = AsyncMock()

        mock_connector = MagicMock()
        mock_connector.id = "conn-uuid-gdrive"
        mock_connector.org_id = 77
        mock_connector.connector_type = "google_drive"
        mock_connector.config = {}
        mock_connector.encrypted_credentials = b"OLD_ENCRYPTED"

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {_PLACEHOLDER_INTERNAL}"}

        # No refresh_token field → default None
        body = CredentialsUpdate(
            access_token="placeholder-new-access-value",
            token_expiry="2026-04-23T12:00:00+00:00",
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
                    "refresh_token": "placeholder-stored-refresh-value",
                }
            )
            mock_store.encrypt_credentials = AsyncMock(
                return_value=(b"NEW_ENCRYPTED", {"access_token": "***", "refresh_token": "***"})
            )

            await update_connector_credentials(
                connector_id="conn-uuid-gdrive",
                body=body,
                request=request,
                db=db,
            )

            call = mock_store.encrypt_credentials.call_args
            merged = call.kwargs.get("config") or (call.args[2] if len(call.args) >= 3 else None)
            assert merged is not None
            # Stored RT preserved (no rotation)
            assert merged["refresh_token"] == "placeholder-stored-refresh-value"

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


# ---------------------------------------------------------------------------
# SPEC-KB-MS-DOCS-001 — Microsoft 365 provider
# ---------------------------------------------------------------------------


class TestMsDocsProvider:
    """ms_docs authorize + callback mirror the google_drive path with Graph endpoints."""

    @pytest.mark.asyncio
    async def test_providers_endpoint_reflects_enabled_ms_docs(self) -> None:
        """ms_docs_client_id set -> ms_docs enabled=True in providers listing."""
        from app.api.oauth import list_providers

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = ""
            mock_settings.ms_docs_client_id = _PLACEHOLDER_CLIENT_ID

            result = await list_providers(user_id="zitadel-user-1")

            assert result["ms_docs"]["enabled"] is True
            assert "offline_access" in result["ms_docs"]["scopes"]
            assert "Files.Read.All" in result["ms_docs"]["scopes"]
            assert "Sites.Read.All" in result["ms_docs"]["scopes"]

    @pytest.mark.asyncio
    async def test_authorize_ms_docs_points_to_microsoft(self) -> None:
        """ms_docs authorize returns a login.microsoftonline.com URL with the expected scopes."""
        import json

        from app.api.oauth import authorize_provider

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = ""
            mock_settings.ms_docs_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.ms_docs_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.ms_docs_tenant_id = "common"
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.portal_url = "https://my.getklai.com"
            mock_settings.domain = "getklai.com"

            response = await authorize_provider(
                provider="ms_docs",
                kb_slug="main",
                user_id="zitadel-user-1",
            )

            assert response.status_code == 200
            body = json.loads(response.body)
            url = body["authorize_url"]
            assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
            assert "scope=offline_access" in url
            assert "Files.Read.All" in url
            assert "Sites.Read.All" in url
            assert "response_type=code" in url
            assert "prompt=consent" in url

    @pytest.mark.asyncio
    async def test_authorize_ms_docs_rejects_when_disabled(self) -> None:
        """Empty ms_docs_client_id -> 404 Not Found."""
        from app.api.oauth import authorize_provider

        with patch("app.api.oauth.settings") as mock_settings:
            mock_settings.google_drive_client_id = ""
            mock_settings.ms_docs_client_id = ""
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY

            with pytest.raises(HTTPException) as exc_info:
                await authorize_provider(
                    provider="ms_docs",
                    kb_slug="main",
                    user_id="zitadel-user-1",
                )

            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_callback_ms_docs_exchanges_code_at_microsoft_endpoint(self) -> None:
        """ms_docs callback POSTs to login.microsoftonline.com/.../token with scope."""
        from app.api.oauth import _sign_state, callback_provider

        db = AsyncMock()

        with (
            patch("app.api.oauth.settings") as mock_settings,
            patch("app.api.oauth.credential_store") as mock_store,
            patch(
                "app.api.oauth.httpx.AsyncClient",
                _mock_httpx_client(
                    _make_http_response(
                        200,
                        {
                            "access_token": "placeholder-access-value",
                            "refresh_token": "placeholder-refresh-value",
                            "expires_in": 3600,
                        },
                    ),
                ),
            ) as mock_httpx_cls,
        ):
            mock_settings.ms_docs_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.ms_docs_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.ms_docs_tenant_id = "common"
            mock_settings.google_drive_client_id = ""
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.portal_url = "https://my.getklai.com"
            mock_settings.domain = "getklai.com"

            state_token = _sign_state(
                {
                    "provider": "ms_docs",
                    "user_id": "zitadel-user-1",
                    "kb_slug": "main",
                    "connector_id": "conn-uuid-ms-1",
                    "nonce": "nonce",
                }
            )

            mock_portal_user = MagicMock()
            mock_portal_user.org_id = 77

            mock_connector = MagicMock()
            mock_connector.id = "conn-uuid-ms-1"
            mock_connector.org_id = 77
            mock_connector.connector_type = "ms_docs"
            mock_connector.config = {}

            db.scalar = AsyncMock(return_value=mock_portal_user)
            db.get = AsyncMock(return_value=mock_connector)
            db.commit = AsyncMock()

            mock_store.encrypt_credentials = AsyncMock(
                return_value=(b"ENCRYPTED_BLOB", {"access_token": "***", "refresh_token": "***"})
            )

            response = await callback_provider(
                provider="ms_docs",
                code="auth-code-ms",
                state=state_token,
                error=None,
                error_description=None,
                klai_oauth_state=state_token,
                user_id="zitadel-user-1",
                db=db,
            )

            assert response.status_code in (302, 303, 307)
            # Token endpoint URL must be the Microsoft one
            post_call = mock_httpx_cls.return_value.post.call_args
            assert "login.microsoftonline.com/common/oauth2/v2.0/token" in post_call.args[0]
            # Scope is forwarded
            assert post_call.kwargs["data"]["scope"] == "offline_access User.Read Files.Read.All Sites.Read.All"
            mock_store.encrypt_credentials.assert_called_once()


# ---------------------------------------------------------------------------
# SPEC-KB-MS-DOCS-001 — connector.reconnect_failed emission
# ---------------------------------------------------------------------------


class TestCallbackReconnectFailed:
    """Callback emits connector.reconnect_failed on consent-denied and token-exchange failure.

    was_reconnect flag is true when the connector already had encrypted_credentials
    AND last_sync_status == "auth_error" (i.e. the user is recovering from a failed
    sync). Only reconnect-flow failures emit the event — first-time failures don't.
    """

    @pytest.mark.asyncio
    async def test_callback_consent_denied_emits_reconnect_failed_and_redirects(self) -> None:
        """Provider redirects with ?error=access_denied (user refused consent).

        Expected: emit connector.reconnect_failed with reason=consent_denied,
        then 302 redirect to portal with ?oauth=failed. No token exchange.
        """
        from app.api.oauth import _sign_state, callback_provider

        db = AsyncMock()

        mock_portal_user = MagicMock()
        mock_portal_user.org_id = 77

        mock_connector = MagicMock()
        mock_connector.id = "conn-uuid-reconnect-denied"
        mock_connector.org_id = 77
        mock_connector.connector_type = "ms_docs"
        mock_connector.config = {}
        # was_reconnect=True requires BOTH of these
        mock_connector.encrypted_credentials = b"EXISTING_ENCRYPTED_BLOB"
        mock_connector.last_sync_status = "auth_error"

        with (
            patch("app.api.oauth.settings") as mock_settings,
            patch("app.api.oauth.emit_event") as mock_emit_event,
        ):
            mock_settings.ms_docs_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.ms_docs_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.ms_docs_tenant_id = "common"
            mock_settings.google_drive_client_id = ""
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.portal_url = "https://my.getklai.com"
            mock_settings.domain = "getklai.com"

            state_token = _sign_state(
                {
                    "provider": "ms_docs",
                    "user_id": "zitadel-user-1",
                    "kb_slug": "main",
                    "connector_id": "conn-uuid-reconnect-denied",
                    "nonce": "nonce",
                }
            )

            db.scalar = AsyncMock(return_value=mock_portal_user)
            db.get = AsyncMock(return_value=mock_connector)

            response = await callback_provider(
                provider="ms_docs",
                code=None,
                state=state_token,
                error="access_denied",
                error_description="The user denied the request.",
                klai_oauth_state=state_token,
                user_id="zitadel-user-1",
                db=db,
            )

            # 302 redirect with ?oauth=failed on the connectors page
            assert response.status_code == 302
            assert "oauth=failed" in response.headers["location"]
            assert "/app/knowledge/main/connectors" in response.headers["location"]

            # Exactly one reconnect_failed event with the expected shape
            mock_emit_event.assert_called_once()
            call = mock_emit_event.call_args
            assert call.args[0] == "connector.reconnect_failed"
            assert call.kwargs["org_id"] == 77
            assert call.kwargs["user_id"] == "zitadel-user-1"
            assert call.kwargs["properties"]["provider"] == "ms_docs"
            assert call.kwargs["properties"]["reason"] == "consent_denied"
            assert call.kwargs["properties"]["provider_error"] == "access_denied"

            # No token exchange happened — no commit, no re-encrypt
            db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_consent_denied_first_time_does_not_emit_reconnect_failed(
        self,
    ) -> None:
        """First-time connection refused (no prior credentials) must NOT emit the
        reconnect_failed event — it's a first-setup abandonment, not a reconnect.
        Still redirects to ?oauth=failed for UX.
        """
        from app.api.oauth import _sign_state, callback_provider

        db = AsyncMock()

        mock_portal_user = MagicMock()
        mock_portal_user.org_id = 77

        mock_connector = MagicMock()
        mock_connector.id = "conn-uuid-first-time"
        mock_connector.org_id = 77
        mock_connector.connector_type = "ms_docs"
        mock_connector.config = {}
        # was_reconnect=False: no prior credentials
        mock_connector.encrypted_credentials = None
        mock_connector.last_sync_status = None

        with (
            patch("app.api.oauth.settings") as mock_settings,
            patch("app.api.oauth.emit_event") as mock_emit_event,
        ):
            mock_settings.ms_docs_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.ms_docs_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.ms_docs_tenant_id = "common"
            mock_settings.google_drive_client_id = ""
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.portal_url = "https://my.getklai.com"
            mock_settings.domain = "getklai.com"

            state_token = _sign_state(
                {
                    "provider": "ms_docs",
                    "user_id": "zitadel-user-1",
                    "kb_slug": "main",
                    "connector_id": "conn-uuid-first-time",
                    "nonce": "nonce",
                }
            )

            db.scalar = AsyncMock(return_value=mock_portal_user)
            db.get = AsyncMock(return_value=mock_connector)

            response = await callback_provider(
                provider="ms_docs",
                code=None,
                state=state_token,
                error="access_denied",
                klai_oauth_state=state_token,
                user_id="zitadel-user-1",
                db=db,
            )

            assert response.status_code == 302
            assert "oauth=failed" in response.headers["location"]
            # No event — this was a first-time setup, not a reconnect
            mock_emit_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_token_exchange_failure_emits_reconnect_failed(self) -> None:
        """Token endpoint returns non-2xx while in a reconnect flow.

        Expected: emit connector.reconnect_failed with reason=token_exchange_failed,
        then raise HTTPException(502).
        """
        from app.api.oauth import _sign_state, callback_provider

        db = AsyncMock()

        mock_portal_user = MagicMock()
        mock_portal_user.org_id = 77

        mock_connector = MagicMock()
        mock_connector.id = "conn-uuid-reconnect-tx-fail"
        mock_connector.org_id = 77
        mock_connector.connector_type = "google_drive"
        mock_connector.config = {}
        # was_reconnect=True
        mock_connector.encrypted_credentials = b"EXISTING_ENCRYPTED_BLOB"
        mock_connector.last_sync_status = "auth_error"

        # Token endpoint returns 400 -> raise_for_status triggers HTTPStatusError
        token_response = _make_http_response(400, {"error": "invalid_grant"})

        with (
            patch("app.api.oauth.settings") as mock_settings,
            patch("app.api.oauth.httpx.AsyncClient", _mock_httpx_client(token_response)),
            patch("app.api.oauth.emit_event") as mock_emit_event,
        ):
            mock_settings.google_drive_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.google_drive_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.portal_url = "https://portal.getklai.com"
            mock_settings.domain = "getklai.com"

            state_token = _sign_state(
                {
                    "provider": "google_drive",
                    "user_id": "zitadel-user-1",
                    "connector_id": "conn-uuid-reconnect-tx-fail",
                }
            )

            db.scalar = AsyncMock(return_value=mock_portal_user)
            db.get = AsyncMock(return_value=mock_connector)

            with pytest.raises(HTTPException) as exc_info:
                await callback_provider(
                    provider="google_drive",
                    code="auth-code-xyz",
                    state=state_token,
                    error=None,
                    error_description=None,
                    klai_oauth_state=state_token,
                    user_id="zitadel-user-1",
                    db=db,
                )

            assert exc_info.value.status_code == 502

            mock_emit_event.assert_called_once()
            call = mock_emit_event.call_args
            assert call.args[0] == "connector.reconnect_failed"
            assert call.kwargs["org_id"] == 77
            assert call.kwargs["user_id"] == "zitadel-user-1"
            assert call.kwargs["properties"]["provider"] == "google_drive"
            assert call.kwargs["properties"]["reason"] == "token_exchange_failed"
            assert call.kwargs["properties"]["provider_status"] == 400

            # No commit on failure
            db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_token_exchange_failure_first_time_does_not_emit(self) -> None:
        """First-time token exchange failure must NOT emit reconnect_failed.

        Still raises 502 — only the event emission is conditional on was_reconnect.
        """
        from app.api.oauth import _sign_state, callback_provider

        db = AsyncMock()

        mock_portal_user = MagicMock()
        mock_portal_user.org_id = 77

        mock_connector = MagicMock()
        mock_connector.id = "conn-uuid-first-time-tx-fail"
        mock_connector.org_id = 77
        mock_connector.connector_type = "google_drive"
        mock_connector.config = {}
        mock_connector.encrypted_credentials = None
        mock_connector.last_sync_status = None

        token_response = _make_http_response(400, {"error": "invalid_grant"})

        with (
            patch("app.api.oauth.settings") as mock_settings,
            patch("app.api.oauth.httpx.AsyncClient", _mock_httpx_client(token_response)),
            patch("app.api.oauth.emit_event") as mock_emit_event,
        ):
            mock_settings.google_drive_client_id = _PLACEHOLDER_CLIENT_ID
            mock_settings.google_drive_client_secret = _PLACEHOLDER_CLIENT_SECRET
            mock_settings.sso_cookie_key = _PLACEHOLDER_COOKIE_KEY
            mock_settings.portal_url = "https://portal.getklai.com"
            mock_settings.domain = "getklai.com"

            state_token = _sign_state(
                {
                    "provider": "google_drive",
                    "user_id": "zitadel-user-1",
                    "connector_id": "conn-uuid-first-time-tx-fail",
                }
            )

            db.scalar = AsyncMock(return_value=mock_portal_user)
            db.get = AsyncMock(return_value=mock_connector)

            with pytest.raises(HTTPException) as exc_info:
                await callback_provider(
                    provider="google_drive",
                    code="auth-code-xyz",
                    state=state_token,
                    error=None,
                    error_description=None,
                    klai_oauth_state=state_token,
                    user_id="zitadel-user-1",
                    db=db,
                )

            assert exc_info.value.status_code == 502
            mock_emit_event.assert_not_called()
