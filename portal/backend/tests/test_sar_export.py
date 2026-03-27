"""
Tests for POST /api/me/sar-export (AVG Art. 15 Subject Access Request).

Pure unit tests — no real DB, all async sessions are mocked.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _mock_org() -> MagicMock:
    org = MagicMock()
    org.moneybird_contact_id = "mb-123"
    return org


def _mock_portal_user() -> MagicMock:
    user = MagicMock()
    user.role = "member"
    user.status = "active"
    user.preferred_language = "nl"
    user.github_username = None
    user.display_name = "Test User"
    user.email = "test@example.com"
    user.kb_retrieval_enabled = True
    user.kb_personal_enabled = True
    user.kb_slugs_filter = None
    user.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    user.librechat_user_id = None
    return user


def _zitadel_user_response() -> dict:
    return {
        "user": {
            "human": {
                "profile": {
                    "firstName": "Test",
                    "lastName": "User",
                    "displayName": "Test User",
                },
                "email": {"email": "test@example.com"},
            },
            "details": {"creationDate": "2024-01-01T00:00:00Z"},
        }
    }


class TestSarExport:
    @pytest.mark.asyncio
    async def test_returns_expected_top_level_keys(self) -> None:
        from app.api.me import sar_export

        org = _mock_org()
        portal_user = _mock_portal_user()

        # Call 1: PortalOrg + PortalUser join query
        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)

        # Calls 2-5: empty .all() for group memberships, KB access, audit, usage events
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []

        # Call 6: meetings — .scalars().all()
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,  # group memberships
            mock_result_empty,  # KB access
            mock_result_empty,  # audit events
            mock_result_empty,  # usage events
            mock_result_meetings,  # meetings
        ]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-123"})
            mock_zitadel.get_user_by_id = AsyncMock(return_value=_zitadel_user_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            result = await sar_export(credentials=mock_credentials, db=db)

        result_dict = result.model_dump()

        assert result_dict["request_user_id"] == "user-123"
        assert "generated_at" in result_dict

        portal = result_dict["klai_portal"]
        assert "identity" in portal
        assert "account" in portal
        assert "group_memberships" in portal
        assert "knowledge_base_access" in portal
        assert "audit_events" in portal
        assert "usage_events" in portal
        assert "meetings" in portal

        ext = result_dict["external_systems"]
        assert "moneybird" in ext
        assert "librechat" in ext
        assert "twenty_crm" in ext

    @pytest.mark.asyncio
    async def test_identity_includes_mfa_status(self) -> None:
        from app.api.me import sar_export

        org = _mock_org()
        portal_user = _mock_portal_user()

        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,  # group memberships
            mock_result_empty,  # KB access
            mock_result_empty,  # audit events
            mock_result_empty,  # usage events
            mock_result_meetings,  # meetings
        ]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-456"})
            mock_zitadel.get_user_by_id = AsyncMock(return_value=_zitadel_user_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=True)

            result = await sar_export(credentials=mock_credentials, db=db)

        assert result.klai_portal.identity.mfa_enrolled is True

    @pytest.mark.asyncio
    async def test_external_systems_include_moneybird_contact_id(self) -> None:
        from app.api.me import sar_export

        org = _mock_org()  # moneybird_contact_id = "mb-123"
        portal_user = _mock_portal_user()

        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,  # group memberships
            mock_result_empty,  # KB access
            mock_result_empty,  # audit events
            mock_result_empty,  # usage events
            mock_result_meetings,  # meetings
        ]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-789"})
            mock_zitadel.get_user_by_id = AsyncMock(return_value=_zitadel_user_response())
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            result = await sar_export(credentials=mock_credentials, db=db)

        assert result.external_systems.moneybird.contact_id == "mb-123"
        assert "mb-123" in result.external_systems.moneybird.note

    @pytest.mark.asyncio
    async def test_user_not_found_returns_404(self) -> None:
        from app.api.me import sar_export

        mock_result_no_user = MagicMock()
        mock_result_no_user.one_or_none.return_value = None

        db = AsyncMock()
        db.execute.side_effect = [mock_result_no_user]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-unknown"})

            with pytest.raises(HTTPException) as exc_info:
                await sar_export(credentials=mock_credentials, db=db)

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "User not found"

    @pytest.mark.asyncio
    async def test_zitadel_identity_fetch_fails_graceful_degradation(self) -> None:
        from app.api.me import sar_export

        org = _mock_org()
        portal_user = _mock_portal_user()

        mock_result_org_user = MagicMock()
        mock_result_org_user.one_or_none.return_value = (org, portal_user)
        mock_result_empty = MagicMock()
        mock_result_empty.all.return_value = []
        mock_result_meetings = MagicMock()
        mock_result_meetings.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute.side_effect = [
            mock_result_org_user,
            mock_result_empty,  # group memberships
            mock_result_empty,  # KB access
            mock_result_empty,  # audit events
            mock_result_empty,  # usage events
            mock_result_meetings,  # meetings
        ]

        mock_credentials = MagicMock()

        with patch("app.api.me.zitadel") as mock_zitadel:
            mock_zitadel.get_userinfo = AsyncMock(return_value={"sub": "user-degrade"})
            mock_zitadel.get_user_by_id = AsyncMock(side_effect=Exception("Zitadel timeout"))
            mock_zitadel.has_any_mfa = AsyncMock(return_value=False)

            result = await sar_export(credentials=mock_credentials, db=db)

        # Identity fields are None when Zitadel call fails (graceful degradation)
        assert result.klai_portal.identity.first_name is None
        assert result.klai_portal.identity.last_name is None
        assert result.klai_portal.identity.display_name is None
        assert result.klai_portal.identity.email is None
        assert result.klai_portal.identity.created_at is None

        # Account and other portal data is still populated normally
        assert result.klai_portal.account.role == "member"
        assert result.klai_portal.account.preferred_language == "nl"
        assert result.klai_portal.group_memberships == []
        assert result.klai_portal.meetings == []
