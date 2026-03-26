"""Tests for scribe knowledge adapter chunking logic.

Tests _cluster_segments, _split_paragraphs, and _detect_content_type
from scribe/scribe-api/app/services/knowledge_adapter.py.
"""
import sys
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# The knowledge_adapter module lives in scribe/scribe-api/ and imports from
# app.core.config which requires the full Scribe app context.  We mock the
# entire app.* package tree so that Python can resolve the import chain, then
# add scribe-api to sys.path so the actual knowledge_adapter module is found.
_scribe_api = str(__import__("pathlib").Path(__file__).resolve().parents[3] / "scribe" / "scribe-api")
sys.path.insert(0, _scribe_api)

_mock_app = ModuleType("app")
_mock_app.__path__ = [_scribe_api + "/app"]  # make it a package
_mock_core = ModuleType("app.core")
_mock_core.__path__ = [_scribe_api + "/app/core"]
_mock_config = ModuleType("app.core.config")
_mock_config.settings = MagicMock(
    knowledge_ingest_url="http://test:9100",
    knowledge_ingest_secret="test-secret",
)
_mock_services = ModuleType("app.services")
_mock_services.__path__ = [_scribe_api + "/app/services"]

sys.modules["app"] = _mock_app
sys.modules["app.core"] = _mock_core
sys.modules["app.core.config"] = _mock_config
sys.modules["app.services"] = _mock_services

from app.services.knowledge_adapter import (  # noqa: E402
    _cluster_segments,
    _detect_content_type,
    _split_paragraphs,
)


# -- _cluster_segments --


def test_cluster_segments_empty():
    assert _cluster_segments([]) == []


def test_cluster_segments_three_segments_one_chunk():
    segments = [
        {"text": "Hello."},
        {"text": "World."},
        {"text": "Test."},
    ]
    result = _cluster_segments(segments)
    assert len(result) == 1
    assert result[0] == "Hello. World. Test."


def test_cluster_segments_six_segments_two_chunks():
    segments = [{"text": f"Segment {i}."} for i in range(6)]
    result = _cluster_segments(segments)
    # 4 segments per chunk target -> 4 + 2
    assert len(result) == 2
    assert "Segment 0." in result[0]
    assert "Segment 4." in result[1]


def test_cluster_segments_long_segment_forces_split():
    # Create a segment that exceeds max_chars (400 * 4 = 1600)
    long_text = "x" * 1700
    segments = [
        {"text": "Short."},
        {"text": long_text},
        {"text": "After."},
    ]
    result = _cluster_segments(segments)
    # "Short." goes into first chunk, then long_text triggers a split
    # because current_chars + len(long_text) > max_chars and current is non-empty
    assert len(result) >= 2


# -- _split_paragraphs --


def test_split_paragraphs_empty():
    assert _split_paragraphs("") == []


def test_split_paragraphs_single():
    result = _split_paragraphs("One paragraph only.")
    assert result == ["One paragraph only."]


def test_split_paragraphs_two():
    result = _split_paragraphs("First paragraph.\n\nSecond paragraph.")
    assert result == ["First paragraph.", "Second paragraph."]


def test_split_paragraphs_strips_whitespace():
    result = _split_paragraphs("  Padded  \n\n  Also padded  ")
    assert result == ["Padded", "Also padded"]


# -- _detect_content_type --


def _make_transcription(recording_type=None):
    t = MagicMock()
    t.recording_type = recording_type
    return t


def test_detect_content_type_meeting():
    t = _make_transcription("meeting")
    assert _detect_content_type(t) == "meeting_transcript"


def test_detect_content_type_recording():
    t = _make_transcription("recording")
    assert _detect_content_type(t) == "1on1_transcript"


def test_detect_content_type_none_defaults_to_meeting():
    t = _make_transcription(None)
    assert _detect_content_type(t) == "meeting_transcript"


def test_detect_content_type_unknown_defaults_to_meeting():
    t = _make_transcription("something_else")
    assert _detect_content_type(t) == "meeting_transcript"
