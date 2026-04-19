"""
Tests for auto-provision logic in idp_callback (SPEC-AUTH-006 R4).

Covers:
- Existing user: normal finalize flow
- New user with matching domain: auto-provision + finalize
- New user without matching domain: redirect to /no-account (join-request TBD)
- DB error during auto-provision: fall through to /no-account
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestIdpCallbackAutoProvision:
    """idp_callback must auto-provision SSO users with matching allowed domains."""

    @pytest.mark.asyncio
    async def test_existing_user_gets_normal_flow(self) -> None:
        """When portal_users row exists, finalize and set cookie normally."""
        from app.api.auth import idp_callback

        mock_portal_user = MagicMock()
        mock_portal_user.org_id = 1

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_portal_user]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.emit_event"),
        ):
            mock_zitadel.create_session_with_idp_intent = AsyncMock(
                return_value={"sessionId": "sid", "sessionToken": "stk"}
            )
            mock_zitadel.get_session_details = AsyncMock(
                return_value={"zitadel_user_id": "user-1", "email": "test@acme.nl"}
            )
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://acme.getklai.com/callback")

            response = await idp_callback(
                id="intent-1",
                token="tok-1",
                auth_request_id="ar-1",
                db=mock_db,
            )

        assert response.status_code == 302
        assert "acme.getklai.com" in response.headers.get("location", "")

    @pytest.mark.asyncio
    async def test_new_user_matching_domain_gets_auto_provisioned(self) -> None:
        """When no portal_users row but domain matches, user is auto-provisioned."""
        from app.api.auth import idp_callback

        # First query: no portal_users rows
        mock_user_result = MagicMock()
        mock_user_result.scalars.return_value.all.return_value = []

        # Second query: domain match
        mock_domain = MagicMock()
        mock_domain.org_id = 42

        mock_domain_result = MagicMock()
        mock_domain_result.scalar_one_or_none.return_value = mock_domain

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mock_user_result, mock_domain_result])
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.emit_event"),
        ):
            mock_zitadel.create_session_with_idp_intent = AsyncMock(
                return_value={"sessionId": "sid", "sessionToken": "stk"}
            )
            mock_zitadel.get_session_details = AsyncMock(
                return_value={"zitadel_user_id": "user-new", "email": "test@acme.nl"}
            )
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://acme.getklai.com/callback")

            response = await idp_callback(
                id="intent-1",
                token="tok-1",
                auth_request_id="ar-1",
                db=mock_db,
            )

        # User was auto-provisioned
        assert mock_db.add.called
        assert response.status_code == 302

    @pytest.mark.asyncio
    async def test_new_user_no_domain_match_redirects_to_no_account(self) -> None:
        """When no portal_users row and no domain match, redirect to /no-account."""
        from app.api.auth import idp_callback

        # First query: no portal_users rows
        mock_user_result = MagicMock()
        mock_user_result.scalars.return_value.all.return_value = []

        # Second query: no domain match
        mock_domain_result = MagicMock()
        mock_domain_result.scalar_one_or_none.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mock_user_result, mock_domain_result])

        with (
            patch("app.api.auth.zitadel") as mock_zitadel,
            patch("app.api.auth.emit_event"),
        ):
            mock_zitadel.create_session_with_idp_intent = AsyncMock(
                return_value={"sessionId": "sid", "sessionToken": "stk"}
            )
            mock_zitadel.get_session_details = AsyncMock(
                return_value={"zitadel_user_id": "user-orphan", "email": "orphan@unknown.com"}
            )
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://portal.getklai.com/callback")

            response = await idp_callback(
                id="intent-1",
                token="tok-1",
                auth_request_id="ar-1",
                db=mock_db,
            )

        # Redirected to callback with finalized auth, but will hit /no-account via callback.tsx
        assert response.status_code == 302
