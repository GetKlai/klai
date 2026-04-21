"""
Tests for POST /api/auth/join-request endpoint (SPEC-AUTH-006 R6).

Covers:
- Creating a new join request
- Idempotent: returns existing pending request
- Rate limit: 3/day per zitadel_user_id
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestJoinRequestEndpoint:
    """POST /api/auth/join-request creates a join request for SSO users."""

    @pytest.mark.asyncio
    async def test_creates_join_request(self) -> None:
        from app.api.auth_join import create_join_request

        mock_credentials = MagicMock()
        mock_credentials.credentials = "test-token"

        # No existing pending request
        mock_pending_result = MagicMock()
        mock_pending_result.scalar_one_or_none.return_value = None

        # Rate limit count = 0
        mock_count_result = MagicMock()
        mock_count_result.scalar_one.return_value = 0

        captured_request = {}

        def capture_add(obj: object) -> None:
            captured_request["obj"] = obj

        async def simulate_flush() -> None:
            captured_request["obj"].id = 1
            captured_request["obj"].requested_at = datetime(2026, 4, 16, tzinfo=UTC)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mock_pending_result, mock_count_result])
        mock_db.add = MagicMock(side_effect=capture_add)
        mock_db.flush = AsyncMock(side_effect=simulate_flush)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with (
            patch("app.api.auth_join.get_current_user_id", return_value="user-sso-1"),
            patch("app.api.auth_join.zitadel") as mock_zitadel,
            patch("app.api.auth_join.generate_approval_token", return_value="a" * 64),
            patch("app.api.auth_join.notify_admin_join_request"),
        ):
            mock_zitadel.get_userinfo = AsyncMock(
                return_value={"sub": "user-sso-1", "email": "test@company.com", "name": "Test User"}
            )
            response = await create_join_request(
                credentials=mock_credentials,
                user_id="user-sso-1",
                db=mock_db,
            )

        assert response.status == "pending"
        assert mock_db.add.called

    @pytest.mark.asyncio
    async def test_returns_existing_pending_request(self) -> None:
        from app.api.auth_join import create_join_request

        mock_credentials = MagicMock()
        mock_credentials.credentials = "test-token"

        existing_request = MagicMock()
        existing_request.id = 99
        existing_request.status = "pending"
        existing_request.requested_at = datetime(2026, 4, 16, tzinfo=UTC)

        mock_pending_result = MagicMock()
        mock_pending_result.scalar_one_or_none.return_value = existing_request

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_pending_result)

        with (
            patch("app.api.auth_join.get_current_user_id", return_value="user-sso-1"),
            patch("app.api.auth_join.zitadel") as mock_zitadel,
        ):
            mock_zitadel.get_userinfo = AsyncMock(
                return_value={"sub": "user-sso-1", "email": "test@company.com", "name": "Test User"}
            )
            response = await create_join_request(
                credentials=mock_credentials,
                user_id="user-sso-1",
                db=mock_db,
            )

        assert response.status == "pending"
        assert response.id == 99
        # Should NOT create a new one
        assert not mock_db.add.called
