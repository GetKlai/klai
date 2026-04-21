"""
Unit tests for recording cleanup -- SPEC-GDPR-002, SPEC-VEXA-001.

Tests the API-based recording cleanup via Vexa meeting-api
DELETE /recordings/{recording_id}.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.recording_cleanup import (
    cleanup_recording,
    delete_recording,
)


def _scoped_session_stub():
    """Build a fake tenant_scoped_session that records the scoped commit.

    `cleanup_recording` now performs the RLS-scoped UPDATE via
    `tenant_scoped_session(meeting.org_id)` so the UPDATE is not silently
    filtered by vexa_meetings' UPDATE policy. Tests need to stub it with
    something that (a) behaves like an async context manager, (b) returns
    a session whose `.execute()` reports rowcount=1, (c) lets us assert the
    org_id that was scoped.
    """
    calls = SimpleNamespace(org_id=None, committed=False)

    @asynccontextmanager
    async def _fake(org_id: int):
        calls.org_id = org_id
        session = AsyncMock()
        result = MagicMock()
        result.rowcount = 1
        session.execute = AsyncMock(return_value=result)

        async def _commit():
            calls.committed = True

        session.commit = _commit
        yield session

    return _fake, calls

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meeting(
    *,
    org_id: int = 1,
    status: str = "done",
    vexa_meeting_id: int | None = 42,
    recording_deleted: bool = False,
    recording_deleted_at: datetime | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    m = MagicMock()
    m.id = uuid.uuid4()
    m.org_id = org_id
    m.status = status
    m.vexa_meeting_id = vexa_meeting_id
    m.recording_deleted = recording_deleted
    m.recording_deleted_at = recording_deleted_at
    m.created_at = created_at or datetime.now(UTC)
    return m


# ---------------------------------------------------------------------------
# delete_recording() via Vexa API
# ---------------------------------------------------------------------------


class TestDeleteRecording:
    @patch("app.services.recording_cleanup.vexa")
    @pytest.mark.anyio
    async def test_success(self, mock_vexa: MagicMock) -> None:
        """Successful API delete returns True."""
        mock_vexa.delete_recording = AsyncMock(return_value=True)
        result = await delete_recording(42, "meeting-uuid")
        assert result is True
        mock_vexa.delete_recording.assert_awaited_once_with(42)

    @patch("app.services.recording_cleanup.vexa")
    @pytest.mark.anyio
    async def test_api_failure(self, mock_vexa: MagicMock) -> None:
        """API returns failure -- returns False, does not raise."""
        mock_vexa.delete_recording = AsyncMock(return_value=False)
        result = await delete_recording(42, "meeting-uuid")
        assert result is False

    @patch("app.services.recording_cleanup.vexa")
    @pytest.mark.anyio
    async def test_unexpected_error(self, mock_vexa: MagicMock) -> None:
        """Generic exception -- returns False, does not raise."""
        mock_vexa.delete_recording = AsyncMock(side_effect=RuntimeError("connection reset"))
        result = await delete_recording(42, "meeting-uuid")
        assert result is False


# ---------------------------------------------------------------------------
# cleanup_recording() happy path
# ---------------------------------------------------------------------------


class TestCleanupRecording:
    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_happy_path_vexa_meeting_id(self, mock_delete: AsyncMock) -> None:
        """Successful cleanup using vexa_meeting_id as fallback.

        Verifies also that the UPDATE is scoped to the meeting's own org_id
        via tenant_scoped_session — the loop runs cross-org, so the UPDATE
        needs its own per-meeting tenant context to satisfy RLS.
        """
        mock_delete.return_value = True
        meeting = _make_meeting(org_id=7)
        db = AsyncMock()
        scoped_factory, scoped_calls = _scoped_session_stub()

        with patch("app.services.recording_cleanup.tenant_scoped_session", scoped_factory):
            await cleanup_recording(meeting, db)

        mock_delete.assert_awaited_once_with(meeting.vexa_meeting_id, str(meeting.id))
        assert scoped_calls.org_id == 7  # tenant was scoped to the meeting's org
        assert scoped_calls.committed is True
        assert meeting.recording_deleted is True
        assert meeting.recording_deleted_at is not None

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_happy_path_recording_id_kwarg(self, mock_delete: AsyncMock) -> None:
        """recording_id kwarg takes precedence over vexa_meeting_id."""
        mock_delete.return_value = True
        meeting = _make_meeting(vexa_meeting_id=42, org_id=3)
        db = AsyncMock()
        scoped_factory, scoped_calls = _scoped_session_stub()

        with patch("app.services.recording_cleanup.tenant_scoped_session", scoped_factory):
            await cleanup_recording(meeting, db, recording_id=99)

        mock_delete.assert_awaited_once_with(99, str(meeting.id))
        assert scoped_calls.org_id == 3
        assert meeting.recording_deleted is True

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_failure_keeps_status_done(self, mock_delete: AsyncMock) -> None:
        """Failed cleanup does not change meeting status, recording_deleted stays False."""
        mock_delete.return_value = False
        meeting = _make_meeting()
        db = AsyncMock()
        scoped_factory, scoped_calls = _scoped_session_stub()

        with patch("app.services.recording_cleanup.tenant_scoped_session", scoped_factory):
            await cleanup_recording(meeting, db)

        assert meeting.status == "done"
        assert meeting.recording_deleted is False
        assert meeting.recording_deleted_at is None
        assert scoped_calls.committed is False  # no tenant session was opened either


# ---------------------------------------------------------------------------
# Guard conditions
# ---------------------------------------------------------------------------


class TestRlsFailLoud:
    """Regression: cleanup_recording must surface the silent-UPDATE bug.

    vexa_meetings has a tenant-scoped UPDATE RLS policy. When the cleanup
    LOOP (which runs cross-org) passed its session down to cleanup_recording
    without setting tenant context, `meeting.recording_deleted = True;
    await db.commit()` silently updated 0 rows. The cleanup then ran forever
    on the same meeting every 5 minutes.
    """

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_zero_rowcount_raises(self, mock_delete: AsyncMock) -> None:
        mock_delete.return_value = True
        meeting = _make_meeting(org_id=99)
        db = AsyncMock()

        @asynccontextmanager
        async def _scoped_no_rows(_org_id: int):
            session = AsyncMock()
            result = MagicMock()
            result.rowcount = 0  # RLS silently filtered the row
            session.execute = AsyncMock(return_value=result)
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.services.recording_cleanup.tenant_scoped_session", _scoped_no_rows),
            pytest.raises(RuntimeError, match="matched 0 rows"),
        ):
            await cleanup_recording(meeting, db)


class TestCleanupGuards:
    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_already_deleted_skips(self, mock_delete: AsyncMock) -> None:
        """recording_deleted=True -- no API call."""
        meeting = _make_meeting(recording_deleted=True)
        db = AsyncMock()

        await cleanup_recording(meeting, db)

        mock_delete.assert_not_awaited()

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_no_vexa_meeting_id_skips(self, mock_delete: AsyncMock) -> None:
        """vexa_meeting_id=None and no recording_id -- no attempt."""
        meeting = _make_meeting(vexa_meeting_id=None)
        db = AsyncMock()

        await cleanup_recording(meeting, db)

        mock_delete.assert_not_awaited()

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_processing_status_skips(self, mock_delete: AsyncMock) -> None:
        """status != 'done' -- no attempt."""
        meeting = _make_meeting(status="processing")
        db = AsyncMock()

        await cleanup_recording(meeting, db)

        mock_delete.assert_not_awaited()

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_no_rid_but_recording_id_kwarg(self, mock_delete: AsyncMock) -> None:
        """vexa_meeting_id=None but recording_id kwarg provided -- uses it."""
        mock_delete.return_value = True
        meeting = _make_meeting(vexa_meeting_id=None)
        db = AsyncMock()
        scoped_factory, _scoped_calls = _scoped_session_stub()

        with patch("app.services.recording_cleanup.tenant_scoped_session", scoped_factory):
            await cleanup_recording(meeting, db, recording_id=77)

        mock_delete.assert_awaited_once_with(77, str(meeting.id))
        assert meeting.recording_deleted is True
