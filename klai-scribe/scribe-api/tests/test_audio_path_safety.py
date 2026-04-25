"""HY-33 / REQ-33 — `_safe_audio_path` path-traversal regression test.

Defense-in-depth partner of HY-34 (sub regex). Even if a future auth flow
returns a malformed `sub`, the path helper MUST refuse to escape the audio
base directory. See SPEC-SEC-HYGIENE-001 REQ-33.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# _safe_audio_path(base, user_id, txn_id) — REQ-33.1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "user_id,txn_id",
    [
        ("269462541789364226", "txn_a1b2c3"),  # Normal Zitadel sub + UUID
        ("X", "txn_short"),                    # Boundary: minimal
    ],
)
def test_safe_audio_path_normal(tmp_path: Path, user_id: str, txn_id: str) -> None:
    from app.services.audio_storage import _safe_audio_path

    base = tmp_path / "audio_base"
    base.mkdir()
    result = _safe_audio_path(base, user_id, txn_id)

    assert result.is_relative_to(base.resolve())
    assert result.name == f"{txn_id}.wav"
    assert user_id in result.parts


@pytest.mark.parametrize(
    "user_id,txn_id",
    [
        ("../../../etc/passwd", "txn_a1"),    # Path traversal in user_id
        ("../evil", "txn_a1"),                # Relative escape
        ("/absolute/path", "txn_a1"),         # Absolute path attempt
        ("..\\win", "txn_a1"),                # Windows-style traversal
        ("user.with.dot", "txn_a1"),          # Defense-in-depth (HY-34 also rejects)
        ("", "txn_a1"),                       # Empty user_id
        ("user", ""),                         # Empty txn_id
        ("user", "../etc"),                   # Traversal in txn_id
    ],
)
def test_safe_audio_path_rejects_traversal(
    tmp_path: Path, user_id: str, txn_id: str
) -> None:
    from app.services.audio_storage import _safe_audio_path

    base = tmp_path / "audio_base"
    base.mkdir()

    with pytest.raises(ValueError, match="invalid audio path"):
        _safe_audio_path(base, user_id, txn_id)


# ---------------------------------------------------------------------------
# _safe_stored_path(base, rel) — REQ-33.2 helper for read / delete
# ---------------------------------------------------------------------------

def test_safe_stored_path_normal(tmp_path: Path) -> None:
    from app.services.audio_storage import _safe_stored_path

    base = tmp_path / "audio_base"
    base.mkdir()
    result = _safe_stored_path(base, "user-1/txn_abc.wav")

    assert result.is_relative_to(base.resolve())
    assert result.name == "txn_abc.wav"


@pytest.mark.parametrize(
    "rel",
    [
        "../../../etc/passwd",
        "../evil/x.wav",
        "/absolute/path.wav",
        "user/../../escape.wav",
        "",
    ],
)
def test_safe_stored_path_rejects_traversal(tmp_path: Path, rel: str) -> None:
    from app.services.audio_storage import _safe_stored_path

    base = tmp_path / "audio_base"
    base.mkdir()

    with pytest.raises(ValueError, match="invalid audio path"):
        _safe_stored_path(base, rel)


# ---------------------------------------------------------------------------
# save_audio integration — REQ-33.2 reroute confirmation
# ---------------------------------------------------------------------------

def test_save_audio_rejects_malformed_user_id(tmp_path: Path, monkeypatch) -> None:
    """save_audio MUST route through _safe_audio_path and refuse traversal."""
    from app.services import audio_storage

    base = tmp_path / "audio_base"
    base.mkdir()
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(base))

    with pytest.raises(ValueError, match="invalid audio path"):
        audio_storage.save_audio(
            user_id="../../../etc/passwd",
            txn_id="txn_evil",
            wav_bytes=b"fake",
        )

    # File MUST NOT exist outside base.
    assert not (tmp_path / "etc" / "passwd").exists()
    assert not Path("/etc/passwd_should_not_be_overwritten").exists()


def test_save_audio_normal_roundtrip(tmp_path: Path, monkeypatch) -> None:
    """Sanity check: legitimate save+read+delete still works after the helper change."""
    from app.services import audio_storage

    base = tmp_path / "audio_base"
    base.mkdir()
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(base))

    rel = audio_storage.save_audio(
        user_id="269462541789364226", txn_id="txn_abc", wav_bytes=b"WAV-DATA"
    )

    assert (base / rel).exists()
    assert audio_storage.read_audio(rel) == b"WAV-DATA"
    audio_storage.delete_audio(rel)
    assert not (base / rel).exists()


def test_read_audio_rejects_malformed_path(tmp_path: Path, monkeypatch) -> None:
    """read_audio MUST validate the stored path even though it came from the DB.

    Defense-in-depth: a corrupted DB row or a future code path that bypasses
    _safe_audio_path on save MUST not become a read primitive.
    """
    from app.services import audio_storage

    base = tmp_path / "audio_base"
    base.mkdir()
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(base))

    with pytest.raises(ValueError, match="invalid audio path"):
        audio_storage.read_audio("../../../etc/passwd")


def test_delete_audio_rejects_malformed_path(tmp_path: Path, monkeypatch) -> None:
    from app.services import audio_storage

    base = tmp_path / "audio_base"
    base.mkdir()
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(base))

    # Create a file outside base — must NOT be deleted.
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"keep me")

    with pytest.raises(ValueError, match="invalid audio path"):
        audio_storage.delete_audio("../outside.wav")
    assert outside.exists()


def test_delete_audio_none_is_noop(tmp_path: Path, monkeypatch) -> None:
    """Existing contract: delete_audio(None) does nothing, no error."""
    from app.services import audio_storage

    base = tmp_path / "audio_base"
    base.mkdir()
    monkeypatch.setattr(audio_storage.settings, "audio_storage_dir", str(base))

    audio_storage.delete_audio(None)  # Should not raise.
