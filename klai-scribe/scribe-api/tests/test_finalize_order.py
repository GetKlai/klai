"""HY-36 / REQ-36 — finalize order + janitor regression tests.

Two concerns covered here:
- REQ-36.1: `finalize_success` deletes the audio file BEFORE mutating the
  record, so a delete failure aborts the operation cleanly (DB still
  references the file, file still on disk — consistent state).
- REQ-36.2: `janitor.sweep_orphans` removes audio files with no matching
  `transcriptions.audio_path` after a grace period, recovering from the
  rare case where delete succeeded but commit crashed.

See SPEC-SEC-HYGIENE-001 REQ-36.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# REQ-36.1 — order: delete BEFORE mutate
# ---------------------------------------------------------------------------

class _FakeResult(SimpleNamespace):
    pass


def _make_record(audio_path: str | None = "user1/txn_a.wav") -> SimpleNamespace:
    return SimpleNamespace(
        status="processing",
        text=None,
        language=None,
        duration_seconds=None,
        inference_time_seconds=None,
        provider=None,
        model=None,
        audio_path=audio_path,
    )


def test_finalize_calls_delete_before_mutate(tmp_path: Path, monkeypatch) -> None:
    """REQ-36.1 — `finalize_success` MUST delete first, mutate second."""
    from app.services import audio_storage

    base = tmp_path / "audio_base"
    base.mkdir()
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(base))

    record = _make_record("user1/txn_a.wav")
    # Track call order: capture record.audio_path at the moment delete is called.
    captured: dict[str, str | None] = {}

    real_safe_stored = audio_storage._safe_stored_path
    delete_calls: list[str | None] = []

    def _spy_delete(audio_path: str | None) -> None:
        delete_calls.append(audio_path)
        captured["audio_path_at_delete"] = record.audio_path
        captured["status_at_delete"] = record.status
        # Run the real delete to keep observable side effects realistic.
        if audio_path:
            real_safe_stored(base, audio_path)

    monkeypatch.setattr(audio_storage, "delete_audio", _spy_delete)

    result = _FakeResult(
        text="hello", language="nl", duration_seconds=1.0,
        inference_time_seconds=0.1, provider="whisper", model="m",
    )
    audio_storage.finalize_success(record, result)

    # Delete fired with the original audio_path BEFORE mutation cleared it.
    assert delete_calls == ["user1/txn_a.wav"]
    assert captured["audio_path_at_delete"] == "user1/txn_a.wav"
    assert captured["status_at_delete"] == "processing"

    # After finalize, mutations applied + audio_path cleared.
    assert record.status == "transcribed"
    assert record.text == "hello"
    assert record.audio_path is None


def test_finalize_aborts_on_delete_failure(tmp_path: Path, monkeypatch) -> None:
    """REQ-36.1 — delete failure aborts; record stays consistent with disk."""
    from app.services import audio_storage

    base = tmp_path / "audio_base"
    base.mkdir()
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(base))

    record = _make_record("user1/txn_a.wav")

    def _failing_delete(audio_path: str | None) -> None:
        raise PermissionError("disk write-locked")

    monkeypatch.setattr(audio_storage, "delete_audio", _failing_delete)

    result = _FakeResult(
        text="hello", language="nl", duration_seconds=1.0,
        inference_time_seconds=0.1, provider="whisper", model="m",
    )

    with pytest.raises(PermissionError):
        audio_storage.finalize_success(record, result)

    # Record was NOT mutated — caller will commit nothing different from before.
    assert record.status == "processing"
    assert record.audio_path == "user1/txn_a.wav"
    assert record.text is None


def test_finalize_with_no_audio_path(tmp_path: Path, monkeypatch) -> None:
    """Sanity: finalize on a record with no audio_path still mutates fields."""
    from app.services import audio_storage

    base = tmp_path / "audio_base"
    base.mkdir()
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(base))

    record = _make_record(audio_path=None)
    result = _FakeResult(
        text="hello", language="nl", duration_seconds=1.0,
        inference_time_seconds=0.1, provider="whisper", model="m",
    )
    audio_storage.finalize_success(record, result)

    assert record.status == "transcribed"
    assert record.text == "hello"


# ---------------------------------------------------------------------------
# REQ-36.2 — janitor sweep
# ---------------------------------------------------------------------------

def _async_session_returning(referenced_paths: list[str]) -> AsyncMock:
    """Build an AsyncSession mock whose execute returns the given audio_paths."""
    session = AsyncMock()
    rows = [(p,) for p in referenced_paths]
    result = MagicMock()
    result.all.return_value = rows
    session.execute.return_value = result
    return session


async def test_janitor_deletes_orphan_after_grace(tmp_path: Path) -> None:
    """REQ-36.2 — orphan file older than grace is deleted."""
    from app.services import janitor

    base = tmp_path / "audio_base"
    base.mkdir()
    orphan = base / "user1" / "orphan.wav"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan-data")
    # Backdate mtime to 25 hours ago.
    old = time.time() - (25 * 3600)
    import os
    os.utime(orphan, (old, old))

    session = _async_session_returning([])  # No DB references
    deleted = await janitor.sweep_orphans(session, base, grace_hours=24)

    assert deleted == 1
    assert not orphan.exists()


async def test_janitor_keeps_orphan_under_grace(tmp_path: Path) -> None:
    """REQ-36.2 — file younger than grace is preserved."""
    from app.services import janitor

    base = tmp_path / "audio_base"
    base.mkdir()
    fresh = base / "user1" / "fresh.wav"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"fresh-data")
    # Default mtime is now — under any grace > 0.

    session = _async_session_returning([])
    deleted = await janitor.sweep_orphans(session, base, grace_hours=24)

    assert deleted == 0
    assert fresh.exists()


async def test_janitor_keeps_referenced_file(tmp_path: Path) -> None:
    """REQ-36.2 — file referenced by transcriptions.audio_path is preserved."""
    from app.services import janitor

    base = tmp_path / "audio_base"
    base.mkdir()
    kept = base / "user1" / "keep.wav"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"keep-data")
    old = time.time() - (25 * 3600)
    import os
    os.utime(kept, (old, old))

    session = _async_session_returning(["user1/keep.wav"])
    deleted = await janitor.sweep_orphans(session, base, grace_hours=24)

    assert deleted == 0
    assert kept.exists()


async def test_janitor_zero_grace_deletes_immediately(tmp_path: Path) -> None:
    """grace_hours=0 collapses the grace check so AC-36 step 11-13 reproduces."""
    from app.services import janitor

    base = tmp_path / "audio_base"
    base.mkdir()
    orphan = base / "user1" / "now.wav"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"now")

    session = _async_session_returning([])
    deleted = await janitor.sweep_orphans(session, base, grace_hours=0)

    assert deleted == 1
    assert not orphan.exists()


async def test_janitor_handles_missing_base(tmp_path: Path) -> None:
    """Janitor on an absent base directory is a no-op, not an error."""
    from app.services import janitor

    base = tmp_path / "does_not_exist"
    session = _async_session_returning([])
    deleted = await janitor.sweep_orphans(session, base, grace_hours=24)

    assert deleted == 0


async def test_janitor_mixed(tmp_path: Path) -> None:
    """Realistic mix: one referenced, one fresh orphan, one old orphan."""
    from app.services import janitor

    base = tmp_path / "audio_base"
    base.mkdir()

    referenced = base / "user1" / "ref.wav"
    referenced.parent.mkdir(parents=True)
    referenced.write_bytes(b"ref")

    fresh_orphan = base / "user2" / "fresh.wav"
    fresh_orphan.parent.mkdir(parents=True)
    fresh_orphan.write_bytes(b"fresh")

    old_orphan = base / "user3" / "old.wav"
    old_orphan.parent.mkdir(parents=True)
    old_orphan.write_bytes(b"old")
    import os
    old = time.time() - (25 * 3600)
    os.utime(old_orphan, (old, old))
    os.utime(referenced, (old, old))  # also old, but referenced — must keep.

    session = _async_session_returning(["user1/ref.wav"])
    deleted = await janitor.sweep_orphans(session, base, grace_hours=24)

    assert deleted == 1
    assert referenced.exists()
    assert fresh_orphan.exists()
    assert not old_orphan.exists()
