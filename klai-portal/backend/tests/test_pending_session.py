"""
Tests for Redis pending-session service (SPEC-AUTH-006 R9).
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestPendingSessionService:
    """Redis-backed pending session for multi-org workspace selection."""

    @pytest.mark.asyncio
    async def test_store_and_retrieve_session(self) -> None:
        from app.services.pending_session import PendingSessionService

        mock_pool = AsyncMock()
        mock_pool.set = AsyncMock()
        mock_pool.get = AsyncMock(
            return_value='{"session_id":"sid","session_token":"stk","zitadel_user_id":"u1","email":"a@b.com","auth_request_id":"ar1","org_ids":[1,2]}'
        )

        with patch("app.services.pending_session.get_redis_pool", return_value=mock_pool):
            svc = PendingSessionService()
            ref = await svc.store(
                session_id="sid",
                session_token="stk",
                zitadel_user_id="u1",
                email="a@b.com",
                auth_request_id="ar1",
                org_ids=[1, 2],
            )
            assert ref  # UUID string

            data = await svc.retrieve(ref)
            assert data is not None
            assert data["session_id"] == "sid"
            assert data["org_ids"] == [1, 2]

    @pytest.mark.asyncio
    async def test_retrieve_missing_returns_none(self) -> None:
        from app.services.pending_session import PendingSessionService

        mock_pool = AsyncMock()
        mock_pool.get = AsyncMock(return_value=None)

        with patch("app.services.pending_session.get_redis_pool", return_value=mock_pool):
            svc = PendingSessionService()
            data = await svc.retrieve("nonexistent-ref")
            assert data is None

    @pytest.mark.asyncio
    async def test_consume_deletes_after_read(self) -> None:
        from app.services.pending_session import PendingSessionService

        mock_pool = AsyncMock()
        mock_pool.get = AsyncMock(
            return_value='{"session_id":"sid","session_token":"stk","zitadel_user_id":"u1","email":"a@b.com","auth_request_id":"ar1","org_ids":[1]}'
        )
        mock_pool.delete = AsyncMock()

        with patch("app.services.pending_session.get_redis_pool", return_value=mock_pool):
            svc = PendingSessionService()
            data = await svc.consume("some-ref")
            assert data is not None
            mock_pool.delete.assert_called_once()
