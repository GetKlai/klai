"""
Parse iCalendar (.ics) data to extract meeting invite details.

Extracts meeting URLs, organizer emails, and scheduling information
from calendar invitations received via IMAP.
"""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from icalendar import Calendar

from app.services.vexa import parse_meeting_url

logger = logging.getLogger(__name__)

# Regex to find meeting URLs in free-text DESCRIPTION fields
_URL_RE = re.compile(
    r"https?://(?:meet\.google\.com/[a-z0-9-]+|(?:[\w-]+\.)?zoom\.us/j/\d+|teams\.microsoft\.com/\S+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ParsedInvite:
    uid: str
    organizer_email: str
    meeting_url: str
    platform: str
    native_meeting_id: str
    dtstart: datetime
    summary: str
    is_cancellation: bool


def parse_ics(ics_bytes: bytes) -> ParsedInvite | None:
    """Parse iCalendar bytes and return a ParsedInvite, or None if no valid meeting URL found."""
    try:
        cal = Calendar.from_ical(ics_bytes)
    except Exception:
        logger.warning("Failed to parse iCalendar data")
        return None

    # Check METHOD for cancellation
    method = str(cal.get("METHOD", "")).upper()
    is_cancellation = method == "CANCEL"

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", ""))
        if not uid:
            continue

        # Parse DTSTART
        dtstart_prop = component.get("DTSTART")
        if dtstart_prop is None:
            continue
        dt = dtstart_prop.dt
        if isinstance(dt, date) and not isinstance(dt, datetime):
            dt = datetime(dt.year, dt.month, dt.day, tzinfo=UTC)
        elif isinstance(dt, datetime):
            if dt.tzinfo is not None:
                dt = dt.astimezone(UTC)
            else:
                dt = dt.replace(tzinfo=UTC)

        # Parse ORGANIZER
        organizer_raw = component.get("ORGANIZER", "")
        organizer_email = str(organizer_raw)
        if organizer_email.lower().startswith("mailto:"):
            organizer_email = organizer_email[7:]

        # Parse SUMMARY
        summary = str(component.get("SUMMARY", ""))

        # Find meeting URL (priority order)
        meeting_url = _extract_meeting_url(component)
        if meeting_url is None:
            logger.warning("No valid meeting URL found in invite UID=%s", uid)
            return None

        ref = parse_meeting_url(meeting_url)
        if ref is None:
            logger.warning("Meeting URL not recognized by any platform: %s", meeting_url)
            return None

        return ParsedInvite(
            uid=uid,
            organizer_email=organizer_email,
            meeting_url=meeting_url,
            platform=ref.platform,
            native_meeting_id=ref.native_meeting_id,
            dtstart=dt,
            summary=summary,
            is_cancellation=is_cancellation,
        )

    logger.warning("No VEVENT found in iCalendar data")
    return None


def _extract_meeting_url(component: object) -> str | None:
    """Extract meeting URL from VEVENT component using priority order."""
    # Priority 1: CONFERENCE property (standard, used by Google Calendar)
    conference = component.get("CONFERENCE")  # type: ignore[union-attr]
    if conference:
        url = str(conference)
        if parse_meeting_url(url) is not None:
            return url

    # Priority 2: X-GOOGLE-CONFERENCE
    x_google = component.get("X-GOOGLE-CONFERENCE")  # type: ignore[union-attr]
    if x_google:
        url = str(x_google)
        if parse_meeting_url(url) is not None:
            return url

    # Priority 3: X-MICROSOFT-SKYPETEAMSMEETINGURL
    x_teams = component.get("X-MICROSOFT-SKYPETEAMSMEETINGURL")  # type: ignore[union-attr]
    if x_teams:
        url = str(x_teams)
        if parse_meeting_url(url) is not None:
            return url

    # Priority 4: Regex search in DESCRIPTION
    description = str(component.get("DESCRIPTION", ""))  # type: ignore[union-attr]
    match = _URL_RE.search(description)
    if match:
        url = match.group(0)
        if parse_meeting_url(url) is not None:
            return url

    return None
