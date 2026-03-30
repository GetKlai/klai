"""Tests for the iCalendar parser service."""

from datetime import UTC, datetime
from pathlib import Path

from app.services.ical_parser import ParsedInvite, parse_ics

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_google_meet_conference_property() -> None:
    """Google Calendar invite with CONFERENCE property is parsed correctly."""
    ics = (FIXTURES / "google_meet.ics").read_bytes()
    result = parse_ics(ics)

    assert result is not None
    assert isinstance(result, ParsedInvite)
    assert result.uid == "abc123@google.com"
    assert result.organizer_email == "alice@example.com"
    assert result.platform == "google_meet"
    assert result.native_meeting_id == "abc-defg-hij"
    assert result.meeting_url == "https://meet.google.com/abc-defg-hij"
    assert result.dtstart == datetime(2026, 4, 1, 14, 0, tzinfo=UTC)
    assert result.summary == "Sprint Planning"
    assert result.is_cancellation is False


def test_parse_zoom_url_in_description() -> None:
    """Zoom invite with URL in DESCRIPTION is parsed correctly."""
    ics = (FIXTURES / "zoom.ics").read_bytes()
    result = parse_ics(ics)

    assert result is not None
    assert result.uid == "zoom-uid-456@zoom.us"
    assert result.organizer_email == "bob@example.com"
    assert result.platform == "zoom"
    assert result.native_meeting_id == "1234567890"
    assert result.summary == "Client Call"
    assert result.is_cancellation is False


def test_parse_teams_x_microsoft_property() -> None:
    """Teams invite with X-MICROSOFT-SKYPETEAMSMEETINGURL is parsed correctly."""
    ics = (FIXTURES / "teams.ics").read_bytes()
    result = parse_ics(ics)

    assert result is not None
    assert result.uid == "teams-uid-789@microsoft.com"
    assert result.organizer_email == "carol@example.com"
    assert result.platform == "teams"
    assert result.summary == "Design Review"
    assert result.is_cancellation is False


def test_parse_cancellation() -> None:
    """METHOD:CANCEL sets is_cancellation=True."""
    ics = (FIXTURES / "cancel.ics").read_bytes()
    result = parse_ics(ics)

    assert result is not None
    assert result.uid == "abc123@google.com"
    assert result.is_cancellation is True


def test_no_meeting_url_returns_none() -> None:
    """An invite without a meeting URL returns None."""
    ics = b"""\
BEGIN:VCALENDAR
VERSION:2.0
METHOD:REQUEST
BEGIN:VEVENT
UID:no-url@example.com
DTSTART:20260401T140000Z
SUMMARY:No Meeting URL
ORGANIZER:mailto:test@example.com
DESCRIPTION:Just a regular event
END:VEVENT
END:VCALENDAR"""
    result = parse_ics(ics)
    assert result is None


def test_dtstart_timezone_conversion_to_utc() -> None:
    """DTSTART with a non-UTC timezone is converted to UTC."""
    ics = (FIXTURES / "zoom.ics").read_bytes()
    result = parse_ics(ics)

    assert result is not None
    # Europe/Amsterdam on April 1 is UTC+2 (CEST)
    assert result.dtstart.tzinfo == UTC
    assert result.dtstart == datetime(2026, 4, 1, 14, 0, tzinfo=UTC)


def test_organizer_mailto_stripping() -> None:
    """ORGANIZER with mailto: prefix has the prefix stripped."""
    ics = (FIXTURES / "google_meet.ics").read_bytes()
    result = parse_ics(ics)

    assert result is not None
    assert result.organizer_email == "alice@example.com"
    assert "mailto:" not in result.organizer_email


def test_invalid_ics_returns_none() -> None:
    """Invalid iCalendar data returns None."""
    result = parse_ics(b"this is not valid ical data")
    assert result is None
