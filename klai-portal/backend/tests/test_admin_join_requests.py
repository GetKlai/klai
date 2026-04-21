"""
Tests for admin join request management endpoints (SPEC-AUTH-006 R8).

Covers:
- GET /api/admin/join-requests (list pending)
- POST /api/admin/join-requests/{id}/approve
- POST /api/admin/join-requests/{id}/deny
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _mock_org(org_id: int = 1) -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.slug = "acme"
    return org


def _mock_admin_user() -> MagicMock:
    user = MagicMock()
    user.role = "admin"
    return user


def _mock_join_request(request_id: int = 1, status: str = "pending") -> MagicMock:
    jr = MagicMock()
    jr.id = request_id
    jr.zitadel_user_id = "user-sso-1"
    jr.email = "test@company.com"
    jr.display_name = "Test User"
    jr.org_id = 1
    jr.status = status
    jr.requested_at = datetime(2026, 4, 16, tzinfo=UTC)
    jr.reviewed_at = None
    jr.reviewed_by = None
    jr.approval_token = "a" * 64
    jr.expires_at = datetime(2026, 4, 23, tzinfo=UTC)
    return jr


class TestListJoinRequests:
    """GET /api/admin/join-requests returns pending requests for org."""

    @pytest.mark.asyncio
    async def test_returns_pending_requests(self) -> None:
        from app.api.admin.join_requests import list_join_requests

        jr = _mock_join_request()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [jr]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        mock_credentials = MagicMock()

        with patch("app.api.admin.join_requests._get_caller_org") as mock_get_org:
            mock_get_org.return_value = ("admin-1", _mock_org(), _mock_admin_user())
            response = await list_join_requests(credentials=mock_credentials, db=mock_db)

        assert len(response.requests) == 1
        assert response.requests[0].email == "test@company.com"


class TestApproveJoinRequest:
    """POST /api/admin/join-requests/{id}/approve creates portal_users row."""

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        from app.api.admin.join_requests import approve_join_request

        mock_db = AsyncMock()
        mock_credentials = MagicMock()
        member = MagicMock()
        member.role = "member"

        with (
            patch("app.api.admin.join_requests._get_caller_org") as mock_get_org,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_get_org.return_value = ("admin-1", _mock_org(), member)
            await approve_join_request(request_id=1, credentials=mock_credentials, db=mock_db, token=None)

        assert exc_info.value.status_code == 403


class TestDenyJoinRequest:
    """POST /api/admin/join-requests/{id}/deny marks request as denied."""

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        from app.api.admin.join_requests import deny_join_request

        mock_db = AsyncMock()
        mock_credentials = MagicMock()
        member = MagicMock()
        member.role = "member"

        with (
            patch("app.api.admin.join_requests._get_caller_org") as mock_get_org,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_get_org.return_value = ("admin-1", _mock_org(), member)
            await deny_join_request(request_id=1, credentials=mock_credentials, db=mock_db)

        assert exc_info.value.status_code == 403
