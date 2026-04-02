"""
Unit tests for recording cleanup -- SPEC-GDPR-002, SPEC-VEXA-001.

Tests the API-based recording cleanup via Vexa meeting-api
DELETE /recordings/{recording_id}.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.recording_cleanup import (
    cleanup_recording,
    delete_recording,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meeting(
    *,
    status: str = "done",
    vexa_meeting_id: int | None = 42,
    recording_deleted: bool = False,
    recording_deleted_at: datetime | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    m = MagicMock()
    m.id = uuid.uuid4()
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
        """Successful cleanup using vexa_meeting_id as fallback."""
        mock_delete.return_value = True
        meeting = _make_meeting()
        db = AsyncMock()

        await cleanup_recording(meeting, db)

        mock_delete.assert_awaited_once_with(meeting.vexa_meeting_id, str(meeting.id))
        assert meeting.recording_deleted is True
        assert meeting.recording_deleted_at is not None
        db.commit.assert_awaited_once()

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_happy_path_recording_id_kwarg(self, mock_delete: AsyncMock) -> None:
        """recording_id kwarg takes precedence over vexa_meeting_id."""
        mock_delete.return_value = True
        meeting = _make_meeting(vexa_meeting_id=42)
        db = AsyncMock()

        await cleanup_recording(meeting, db, recording_id=99)

        mock_delete.assert_awaited_once_with(99, str(meeting.id))
        assert meeting.recording_deleted is True

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_failure_keeps_status_done(self, mock_delete: AsyncMock) -> None:
        """Failed cleanup does not change meeting status, recording_deleted stays False."""
        mock_delete.return_value = False
        meeting = _make_meeting()
        db = AsyncMock()

        await cleanup_recording(meeting, db)

        assert meeting.status == "done"
        assert meeting.recording_deleted is False
        assert meeting.recording_deleted_at is None
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Guard conditions
# ---------------------------------------------------------------------------


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

        await cleanup_recording(meeting, db, recording_id=77)

        mock_delete.assert_awaited_once_with(77, str(meeting.id))
        assert meeting.recording_deleted is True
