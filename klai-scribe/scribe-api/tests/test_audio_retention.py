"""Audio retention policy: WAV file must be removed after successful transcription.

Bug discovered 2026-04-21: the cleanup helper was only called from the user-initiated
DELETE /transcriptions/{id} endpoint, never from the happy path of POST /transcribe
or POST /transcriptions/{id}/retry. Files accumulated on disk — confirmed 1 orphan
WAV from 2026-04-10 still present on production.

Policy (stated by product owner 2026-04-21): audio must be removed as soon as
transcription succeeds. Audio is retained on `failed` status so retry can replay.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services import audio_storage


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """Redirect audio_storage_dir to a per-test tmp dir so the real volume isn't touched."""
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(tmp_path), raising=False)
    yield tmp_path


class TestSaveThenDelete:
    def test_roundtrip_removes_file(self, _isolated_storage) -> None:
        rel = audio_storage.save_audio("user-1", "txn_abc", b"RIFFWAVdata")
        saved = _isolated_storage / rel
        assert saved.exists(), "precondition: save_audio writes to disk"

        audio_storage.delete_audio(rel)
        assert not saved.exists(), "delete_audio must remove the file"

    def test_delete_with_none_is_noop(self) -> None:
        # Must not raise on None — retry endpoint calls with optional path.
        audio_storage.delete_audio(None)

    def test_delete_missing_file_is_noop(self) -> None:
        # unlink(missing_ok=True) — idempotent across retries.
        audio_storage.delete_audio("user-1/does-not-exist.wav")


class TestFinalizeSuccess:
    def test_audio_deleted_and_path_cleared(self, _isolated_storage) -> None:
        """After successful transcription, the WAV must be gone and audio_path
        nulled so no retry logic can find it."""
        rel = audio_storage.save_audio("user-1", "txn_success", b"RIFFWAVdata")
        audio_file = _isolated_storage / rel
        assert audio_file.exists()

        record = MagicMock()
        record.audio_path = rel

        result = MagicMock()
        result.text = "hallo wereld"
        result.language = "nl"
        result.duration_seconds = 1.5
        result.inference_time_seconds = 0.3
        result.provider = "vexa-transcription-service"
        result.model = "large-v3-turbo"

        audio_storage.finalize_success(record, result)

        assert not audio_file.exists(), "audio must be deleted after successful transcription"
        assert record.audio_path is None, "record.audio_path must be cleared"
        assert record.status == "transcribed"
        assert record.text == "hallo wereld"
        assert record.language == "nl"
        assert record.provider == "vexa-transcription-service"

    def test_idempotent_when_file_is_already_missing(self, _isolated_storage) -> None:
        """Double-finalize or race — must not crash."""
        record = MagicMock()
        record.audio_path = "user-1/txn_ghost.wav"

        result = MagicMock()
        result.text = "x"
        result.language = "nl"
        result.duration_seconds = 0.1
        result.inference_time_seconds = 0.05
        result.provider = "p"
        result.model = "m"

        audio_storage.finalize_success(record, result)

        assert record.audio_path is None
        assert record.status == "transcribed"

    def test_safe_when_record_has_no_audio_path(self, _isolated_storage) -> None:
        """Record that never had an audio file (defensive — shouldn't happen but survives)."""
        record = MagicMock()
        record.audio_path = None

        result = MagicMock()
        result.text = "x"
        result.language = "nl"
        result.duration_seconds = 0.0
        result.inference_time_seconds = 0.0
        result.provider = "p"
        result.model = "m"

        audio_storage.finalize_success(record, result)

        assert record.audio_path is None
        assert record.status == "transcribed"
