"""
Unit tests for meeting utility functions.
Pure tests -- no DB, no HTTP.
"""

from app.api.meetings import SpeakerEvent, _correlate_speakers
from app.services.vexa import parse_meeting_url

# -- parse_meeting_url --------------------------------------------------------


def test_parse_meeting_url_google_meet() -> None:
    ref = parse_meeting_url("https://meet.google.com/abc-defg-hij")
    assert ref is not None
    assert ref.platform == "google_meet"
    assert ref.native_meeting_id == "abc-defg-hij"


def test_parse_meeting_url_google_meet_case_insensitive() -> None:
    ref = parse_meeting_url("HTTPS://MEET.GOOGLE.COM/abc-defg-hij")
    assert ref is not None
    assert ref.platform == "google_meet"


def test_parse_meeting_url_zoom() -> None:
    ref = parse_meeting_url("https://us02web.zoom.us/j/1234567890")
    assert ref is not None
    assert ref.platform == "zoom"
    assert ref.native_meeting_id == "1234567890"


def test_parse_meeting_url_zoom_plain() -> None:
    ref = parse_meeting_url("https://zoom.us/j/9876543210")
    assert ref is not None
    assert ref.platform == "zoom"
    assert ref.native_meeting_id == "9876543210"


def test_parse_meeting_url_teams() -> None:
    url = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc123"
    ref = parse_meeting_url(url)
    assert ref is not None
    assert ref.platform == "teams"
    # Teams uses a SHA-256 hash prefix as the ID
    assert len(ref.native_meeting_id) == 32


def test_parse_meeting_url_invalid() -> None:
    assert parse_meeting_url("https://example.com/meeting") is None
    assert parse_meeting_url("not a url") is None
    assert parse_meeting_url("") is None


# -- _correlate_speakers ------------------------------------------------------


def test_correlate_speakers_with_events() -> None:
    segments = [
        {"start": 0.0, "end": 5.0, "text": "Hello"},
        {"start": 6.0, "end": 10.0, "text": "Hi there"},
        {"start": 12.0, "end": 15.0, "text": "Let us begin"},
    ]
    events = [
        SpeakerEvent(timestamp=0.0, participant_name="Alice"),
        SpeakerEvent(timestamp=5.5, participant_name="Bob"),
        SpeakerEvent(timestamp=11.0, participant_name="Alice"),
    ]
    result = _correlate_speakers(segments, events, duration_seconds=15.0)

    assert len(result) == 3
    assert result[0]["speaker"] == "Alice"
    assert result[1]["speaker"] == "Bob"
    assert result[2]["speaker"] == "Alice"
    # Original segment data is preserved
    assert result[0]["text"] == "Hello"


def test_correlate_speakers_unknown_fallback() -> None:
    segments = [
        {"start": 0.0, "end": 3.0, "text": "First"},
        {"start": 4.0, "end": 6.0, "text": "Second"},
    ]
    events = [
        SpeakerEvent(timestamp=0.0, participant_name=None),
        SpeakerEvent(timestamp=3.5, participant_name=None),
    ]
    result = _correlate_speakers(segments, events, duration_seconds=6.0)

    assert len(result) == 2
    # Unknown speakers get "Deelnemer N" labels
    assert result[0]["speaker"].startswith("Deelnemer")
    assert result[1]["speaker"].startswith("Deelnemer")


def test_correlate_speakers_empty_events() -> None:
    segments = [
        {"start": 0.0, "end": 5.0, "text": "Hello"},
        {"start": 6.0, "end": 10.0, "text": "World"},
    ]
    result = _correlate_speakers(segments, speaker_events=[], duration_seconds=10.0)

    # With no speaker events, segments are returned unchanged
    assert result == segments
    # Verify no "speaker" key was added
    assert "speaker" not in result[0]


def test_correlate_speakers_mixed_known_unknown() -> None:
    segments = [
        {"start": 0.0, "end": 3.0, "text": "Intro"},
        {"start": 5.0, "end": 8.0, "text": "Response"},
    ]
    events = [
        SpeakerEvent(timestamp=0.0, participant_name="Alice"),
        SpeakerEvent(timestamp=4.0, participant_name=None),
    ]
    result = _correlate_speakers(segments, events, duration_seconds=8.0)

    assert result[0]["speaker"] == "Alice"
    assert result[1]["speaker"].startswith("Deelnemer")
