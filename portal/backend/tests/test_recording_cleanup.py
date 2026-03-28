"""
Unit tests for recording cleanup -- SPEC-GDPR-002.

Pure tests using mocks for Docker SDK and AsyncSession.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import docker.errors
import pytest

from app.services.recording_cleanup import (
    RECORDINGS_BASE_PATH,
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
# S4: delete_recording() exec_run success
# ---------------------------------------------------------------------------


class TestDeleteRecording:
    @patch("app.services.recording_cleanup._sync_delete_recording")
    @pytest.mark.anyio
    async def test_success(self, mock_sync: MagicMock) -> None:
        """S4: successful exec_run returns True and logs info."""
        mock_sync.return_value = (0, "")
        result = await delete_recording(42, "meeting-uuid")
        assert result is True
        mock_sync.assert_called_once_with(42)

    @patch("app.services.recording_cleanup._sync_delete_recording")
    @pytest.mark.anyio
    async def test_nonzero_exit_code(self, mock_sync: MagicMock) -> None:
        """exec_run returns non-zero exit code -- returns False."""
        mock_sync.return_value = (1, "permission denied")
        result = await delete_recording(42, "meeting-uuid")
        assert result is False

    @patch("app.services.recording_cleanup._sync_delete_recording")
    @pytest.mark.anyio
    async def test_container_not_found(self, mock_sync: MagicMock) -> None:
        """S3: container not reachable -- returns False, does not raise."""
        mock_sync.side_effect = docker.errors.NotFound("not found")
        result = await delete_recording(42, "meeting-uuid")
        assert result is False

    @patch("app.services.recording_cleanup._sync_delete_recording")
    @pytest.mark.anyio
    async def test_unexpected_error(self, mock_sync: MagicMock) -> None:
        """Generic exception -- returns False, does not raise."""
        mock_sync.side_effect = RuntimeError("connection reset")
        result = await delete_recording(42, "meeting-uuid")
        assert result is False


# ---------------------------------------------------------------------------
# S5: delete_recording() idempotent (missing path)
# ---------------------------------------------------------------------------


class TestDeleteRecordingIdempotent:
    @patch("app.services.recording_cleanup._sync_delete_recording")
    @pytest.mark.anyio
    async def test_missing_path_is_success(self, mock_sync: MagicMock) -> None:
        """S5: rm -rf on non-existent path returns exit 0 -- treated as success."""
        mock_sync.return_value = (0, "")
        result = await delete_recording(99, "meeting-uuid")
        assert result is True


# ---------------------------------------------------------------------------
# S1/S2: cleanup_recording() happy path
# ---------------------------------------------------------------------------


class TestCleanupRecording:
    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_happy_path(self, mock_delete: AsyncMock) -> None:
        """S1: successful cleanup sets recording_deleted=True and commits."""
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
    async def test_failure_keeps_status_done(self, mock_delete: AsyncMock) -> None:
        """S3: failed cleanup does not change meeting status, recording_deleted stays False."""
        mock_delete.return_value = False
        meeting = _make_meeting()
        db = AsyncMock()

        await cleanup_recording(meeting, db)

        assert meeting.status == "done"
        assert meeting.recording_deleted is False
        assert meeting.recording_deleted_at is None
        db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# S10: Guard -- already deleted
# ---------------------------------------------------------------------------


class TestCleanupGuards:
    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_already_deleted_skips(self, mock_delete: AsyncMock) -> None:
        """S10: recording_deleted=True -- no Docker exec."""
        meeting = _make_meeting(recording_deleted=True)
        db = AsyncMock()

        await cleanup_recording(meeting, db)

        mock_delete.assert_not_awaited()

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_no_vexa_meeting_id_skips(self, mock_delete: AsyncMock) -> None:
        """S11: vexa_meeting_id=None -- no attempt."""
        meeting = _make_meeting(vexa_meeting_id=None)
        db = AsyncMock()

        await cleanup_recording(meeting, db)

        mock_delete.assert_not_awaited()

    @patch("app.services.recording_cleanup.delete_recording", new_callable=AsyncMock)
    @pytest.mark.anyio
    async def test_processing_status_skips(self, mock_delete: AsyncMock) -> None:
        """S12: status != 'done' -- no attempt."""
        meeting = _make_meeting(status="processing")
        db = AsyncMock()

        await cleanup_recording(meeting, db)

        mock_delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# _sync_delete_recording uses correct rm command
# ---------------------------------------------------------------------------


class TestSyncDeleteRecording:
    @patch("app.services.recording_cleanup.docker")
    def test_exec_run_command(self, mock_docker: MagicMock) -> None:
        """S4: verify the exact rm command and container lookup."""
        from app.services.recording_cleanup import _sync_delete_recording

        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, b"")
        mock_docker.from_env.return_value.containers.get.return_value = mock_container

        exit_code, _output = _sync_delete_recording(42)

        mock_container.exec_run.assert_called_once_with(
            ["rm", "-rf", f"{RECORDINGS_BASE_PATH}/42"],
            stdout=True,
            stderr=True,
        )
        assert exit_code == 0
