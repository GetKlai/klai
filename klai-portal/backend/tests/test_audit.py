"""
Tests for audit log service (app/services/audit.py)

The service uses raw SQL with an independent AsyncSessionLocal session.
All tests mock AsyncSessionLocal to verify correct SQL params are passed.
"""

from unittest.mock import AsyncMock, patch

import pytest


def _mock_session() -> AsyncMock:
    """Create a mock async session with context manager support."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestLogEvent:
    @pytest.mark.asyncio
    async def test_creates_audit_entry(self) -> None:
        from app.services.audit import log_event

        session = _mock_session()

        with patch("app.services.audit.AsyncSessionLocal", return_value=session):
            await log_event(
                org_id=1,
                actor="alice",
                action="meeting.created",
                resource_type="meeting",
                resource_id="m1",
                details={"group_id": 42},
            )

        session.execute.assert_called_once()
        session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_audit_entry_fields(self) -> None:
        from app.services.audit import log_event

        session = _mock_session()

        with patch("app.services.audit.AsyncSessionLocal", return_value=session):
            await log_event(
                org_id=5,
                actor="admin-1",
                action="group.member_added",
                resource_type="group",
                resource_id="42",
                details={"user_id": "bob"},
            )

        params = session.execute.call_args[0][1]
        assert params["org_id"] == 5
        assert params["actor"] == "admin-1"
        assert params["action"] == "group.member_added"
        assert params["resource_type"] == "group"
        assert params["resource_id"] == "42"
        assert params["details"] == '{"user_id": "bob"}'

    @pytest.mark.asyncio
    async def test_log_event_is_non_fatal_on_db_error(self) -> None:
        """Audit log failures must not raise -- they are non-fatal."""
        from app.services.audit import log_event

        session = _mock_session()
        session.execute.side_effect = Exception("DB connection lost")

        with patch("app.services.audit.AsyncSessionLocal", return_value=session):
            # Should not raise
            await log_event(
                org_id=1,
                actor="alice",
                action="meeting.created",
                resource_type="meeting",
                resource_id="m1",
            )

    @pytest.mark.asyncio
    async def test_log_event_without_details(self) -> None:
        from app.services.audit import log_event

        session = _mock_session()

        with patch("app.services.audit.AsyncSessionLocal", return_value=session):
            await log_event(
                org_id=1,
                actor="alice",
                action="user.suspended",
                resource_type="user",
                resource_id="alice-id",
            )

        params = session.execute.call_args[0][1]
        assert params["details"] is None

    @pytest.mark.asyncio
    async def test_resource_id_is_always_string(self) -> None:
        """resource_id must be stored as string even if passed as int/UUID."""
        from app.services.audit import log_event

        session = _mock_session()

        with patch("app.services.audit.AsyncSessionLocal", return_value=session):
            await log_event(
                org_id=1,
                actor="alice",
                action="meeting.created",
                resource_type="meeting",
                resource_id=12345,  # type: ignore[arg-type]
            )

        params = session.execute.call_args[0][1]
        assert isinstance(params["resource_id"], str)
        assert params["resource_id"] == "12345"
