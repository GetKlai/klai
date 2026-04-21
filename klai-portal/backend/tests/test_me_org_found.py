"""
Tests for the org_found field in MeResponse (SPEC-AUTH-006 R1).

Verifies that /api/me returns org_found=True when a portal_users row exists
for the authenticated user, and org_found=False when no row exists.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_org() -> MagicMock:
    org = MagicMock()
    org.slug = "acme"
    org.provisioning_status = "ready"
    org.mfa_policy = "optional"
    org.moneybird_contact_id = "mb-1"
    return org


def _mock_portal_user() -> MagicMock:
    user = MagicMock()
    user.role = "member"
    user.preferred_language = "nl"
    user.display_name = "Test User"
    user.email = "test@acme.nl"
    return user


class TestMeOrgFound:
    """org_found field must be True when a portal_users row exists, False otherwise."""

    @pytest.mark.asyncio
    async def test_org_found_true_when_portal_user_exists(self) -> None:
        """When portal_users row exists for zitadel_user_id, org_found should be True."""
        from app.api.me import me

        org = _mock_org()
        portal_user = _mock_portal_user()

        mock_row = MagicMock()
        mock_row.__iter__ = lambda self: iter((org, portal_user))

        mock_result = MagicMock()
        mock_result.one_or_none.return_value = mock_row

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()

        mock_credentials = MagicMock()
        mock_credentials.credentials = "test-token"

        userinfo = {
            "sub": "user-123",
            "email": "test@acme.nl",
            "name": "Test User",
        }

        with (
            patch("app.api.me.zitadel") as mock_zitadel,
            patch("app.api.me.get_effective_products", return_value=[]),
        ):
            mock_zitadel.get_userinfo = AsyncMock(return_value=userinfo)
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            response = await me(credentials=mock_credentials, db=mock_db)

        assert response.org_found is True

    @pytest.mark.asyncio
    async def test_org_found_false_when_no_portal_user(self) -> None:
        """When no portal_users row exists for zitadel_user_id, org_found should be False."""
        from app.api.me import me

        mock_result = MagicMock()
        mock_result.one_or_none.return_value = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_credentials = MagicMock()
        mock_credentials.credentials = "test-token"

        userinfo = {
            "sub": "user-456",
            "email": "nobody@example.com",
            "name": "Nobody",
        }

        with (
            patch("app.api.me.zitadel") as mock_zitadel,
            patch("app.api.me.get_effective_products", return_value=[]),
        ):
            mock_zitadel.get_userinfo = AsyncMock(return_value=userinfo)
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            response = await me(credentials=mock_credentials, db=mock_db)

        assert response.org_found is False

    @pytest.mark.asyncio
    async def test_org_found_in_response_model(self) -> None:
        """MeResponse model should include org_found field with default False."""
        from app.api.me import MeResponse

        # Default should be False
        resp = MeResponse(user_id="u1", email="a@b.com", name="A")
        assert resp.org_found is False

        # Can be set to True
        resp2 = MeResponse(user_id="u1", email="a@b.com", name="A", org_found=True)
        assert resp2.org_found is True
