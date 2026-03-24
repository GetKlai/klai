"""
Tests for SPEC-AUTH-003: Audit log service (app/services/audit.py)

Pure unit tests -- all async sessions are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestLogEvent:
    @pytest.mark.asyncio
    async def test_creates_audit_entry(self) -> None:
        from app.services.audit import log_event

        db = AsyncMock()
        db.add = MagicMock()

        await log_event(
            db,
            org_id=1,
            actor="alice",
            action="meeting.created",
            resource_type="meeting",
            resource_id="m1",
            details={"group_id": 42},
        )

        # Verify an entry was added to the session
        db.add.assert_called_once()
        db.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_audit_entry_fields(self) -> None:
        from app.models.audit import PortalAuditLog
        from app.services.audit import log_event

        db = AsyncMock()
        added_entry = None

        def capture_add(entry: object) -> None:
            nonlocal added_entry
            added_entry = entry

        # db.add is synchronous in SQLAlchemy -- override with a regular MagicMock
        db.add = MagicMock(side_effect=capture_add)

        await log_event(
            db,
            org_id=5,
            actor="admin-1",
            action="group.member_added",
            resource_type="group",
            resource_id="42",
            details={"user_id": "bob"},
        )

        assert added_entry is not None
        assert isinstance(added_entry, PortalAuditLog)
        assert added_entry.org_id == 5
        assert added_entry.actor_user_id == "admin-1"
        assert added_entry.action == "group.member_added"
        assert added_entry.resource_type == "group"
        assert added_entry.resource_id == "42"
        assert added_entry.details == {"user_id": "bob"}

    @pytest.mark.asyncio
    async def test_log_event_is_non_fatal_on_flush_error(self) -> None:
        """Audit log failures must not raise -- they are non-fatal."""
        from app.services.audit import log_event

        db = AsyncMock()
        db.add = MagicMock()
        db.flush.side_effect = Exception("DB connection lost")

        # Should not raise
        await log_event(
            db,
            org_id=1,
            actor="alice",
            action="meeting.created",
            resource_type="meeting",
            resource_id="m1",
        )

    @pytest.mark.asyncio
    async def test_log_event_without_details(self) -> None:
        from app.models.audit import PortalAuditLog
        from app.services.audit import log_event

        db = AsyncMock()
        added_entry = None

        def capture_add(entry: object) -> None:
            nonlocal added_entry
            added_entry = entry

        db.add = MagicMock(side_effect=capture_add)

        await log_event(
            db,
            org_id=1,
            actor="alice",
            action="user.suspended",
            resource_type="user",
            resource_id="alice-id",
        )

        assert added_entry is not None
        assert added_entry.details is None

    @pytest.mark.asyncio
    async def test_resource_id_is_always_string(self) -> None:
        """resource_id must be stored as string even if passed as int/UUID."""
        from app.models.audit import PortalAuditLog
        from app.services.audit import log_event

        db = AsyncMock()
        added_entry = None

        def capture_add(entry: object) -> None:
            nonlocal added_entry
            added_entry = entry

        db.add = MagicMock(side_effect=capture_add)

        await log_event(
            db,
            org_id=1,
            actor="alice",
            action="meeting.created",
            resource_type="meeting",
            resource_id=12345,  # type: ignore[arg-type]
        )

        assert isinstance(added_entry.resource_id, str)
        assert added_entry.resource_id == "12345"
