"""
Tests for admin join request management endpoints (SPEC-AUTH-006 R8).

Covers:
- GET /api/admin/join-requests (list pending)
- POST /api/admin/join-requests/{id}/approve
- POST /api/admin/join-requests/{id}/deny

SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2b:
- list_join_requests + deny_join_request use ``Depends(get_caller_at_least(ADMIN))``;
  non-admin 403 is pinned in test_permissions.py.
- approve_join_request keeps an OPTIONAL Bearer (token-based path needs no
  auth) and resolves the caller via the shared `_resolve_caller_with_options`
  helper. The non-admin 403 lives inside the endpoint and is tested here.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms


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

        response = await list_join_requests(
            perms=make_perms(role="admin", org_id=1),
            db=mock_db,
        )

        assert len(response.requests) == 1
        assert response.requests[0].email == "test@company.com"


class TestApproveJoinRequest:
    """POST /api/admin/join-requests/{id}/approve creates portal_users row."""

    @pytest.mark.asyncio
    async def test_non_admin_rejected(self) -> None:
        """Bearer-based approval must reject non-admin callers.

        approve_join_request takes an OPTIONAL Bearer (the token-based
        email-link path needs no auth), so the role check is inline in
        the endpoint, not via `get_caller_at_least`. We mock the resolver
        helper to return a non-admin perms snapshot.
        """
        from app.api.admin.join_requests import approve_join_request

        mock_db = AsyncMock()
        mock_credentials = MagicMock()
        non_admin_perms = make_perms(role="company", org_id=1)

        with patch(
            "app.api.admin.join_requests._resolve_caller_with_options",
            new=AsyncMock(return_value=non_admin_perms),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await approve_join_request(
                    request_id=1,
                    credentials=mock_credentials,
                    db=mock_db,
                    token=None,
                )

        assert exc_info.value.status_code == 403


class TestDenyJoinRequest:
    """POST /api/admin/join-requests/{id}/deny marks request as denied.

    Phase 2b: the non-admin 403 branch is now enforced by
    ``Depends(get_caller_at_least(ProfileRole.ADMIN))`` and pinned in
    `test_permissions.py::test_get_caller_at_least_role_matrix`. The
    test here was a duplicate of that role-matrix coverage; it has been
    removed in favour of a happy-path smoke test that exercises the
    actual deny flow against a pending request.
    """

    @pytest.mark.asyncio
    async def test_admin_marks_request_denied(self) -> None:
        from app.api.admin.join_requests import deny_join_request

        jr = _mock_join_request()
        result = MagicMock()
        result.scalar_one_or_none.return_value = jr

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=result)

        await deny_join_request(
            request_id=1,
            perms=make_perms(role="admin", user_id="admin-1", org_id=1),
            db=mock_db,
        )

        assert jr.status == "denied"
        assert jr.reviewed_by == "admin-1"
        mock_db.commit.assert_awaited_once()
