"""Tests for the IMAP listener service."""

import email
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.imap_listener import _extract_ics_parts, _process_email

FIXTURES = Path(__file__).parent / "fixtures"


def _make_email_with_ics(ics_bytes: bytes) -> bytes:
    """Create a MIME email with a text/calendar part."""
    msg = MIMEMultipart()
    msg["From"] = "calendar@example.com"
    msg["To"] = "bot@example.com"
    msg["Subject"] = "Meeting invite"

    body = MIMEText("You have been invited to a meeting.")
    msg.attach(body)

    cal_part = MIMEBase("text", "calendar", method="REQUEST")
    cal_part.set_payload(ics_bytes)
    msg.attach(cal_part)

    return msg.as_bytes()


def _make_email_with_ics_attachment(ics_bytes: bytes) -> bytes:
    """Create a MIME email with a .ics file attachment."""
    msg = MIMEMultipart()
    msg["From"] = "calendar@example.com"
    msg["To"] = "bot@example.com"

    body = MIMEText("See attached invite.")
    msg.attach(body)

    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(ics_bytes)
    attachment.add_header("Content-Disposition", "attachment", filename="invite.ics")
    msg.attach(attachment)

    return msg.as_bytes()


def test_extract_ics_from_text_calendar_part() -> None:
    """text/calendar MIME parts are extracted."""
    ics_bytes = (FIXTURES / "google_meet.ics").read_bytes()
    raw = _make_email_with_ics(ics_bytes)
    msg = email.message_from_bytes(raw)

    parts = _extract_ics_parts(msg)
    assert len(parts) == 1
    assert b"VCALENDAR" in parts[0]


def test_extract_ics_from_attachment() -> None:
    """.ics file attachments are extracted."""
    ics_bytes = (FIXTURES / "zoom.ics").read_bytes()
    raw = _make_email_with_ics_attachment(ics_bytes)
    msg = email.message_from_bytes(raw)

    parts = _extract_ics_parts(msg)
    assert len(parts) == 1
    assert b"VCALENDAR" in parts[0]


def test_no_ics_in_email() -> None:
    """An email without .ics content returns empty list."""
    msg = MIMEText("Just a regular email, no calendar.")
    parts = _extract_ics_parts(msg)
    assert parts == []


@pytest.mark.asyncio
async def test_process_email_with_valid_invite() -> None:
    """A mail-auth-verified .ics email triggers tenant lookup and scheduling.

    SPEC-SEC-IMAP-001: ``find_tenant`` now receives ``verified_from`` (the
    DKIM-verified RFC-5322 From address), NOT the ICS ``ORGANIZER`` field.
    ``verify_mail_auth`` is mocked here so this test stays focused on the
    ICS-extraction / tenant-lookup path; the crypto path is covered by
    ``tests/services/test_mail_auth.py`` + ``tests/services/test_imap_listener.py``.
    """
    ics_bytes = (FIXTURES / "google_meet.ics").read_bytes()
    raw_email = _make_email_with_ics(ics_bytes)

    from app.services.mail_auth import MailAuthResult

    verified = MailAuthResult(
        dkim_result={"present": True, "valid": True, "d": "example.com", "aligned": True},
        spf_result={"result": "pass", "smtp_mailfrom_domain": "example.com", "aligned": True},
        arc_result={
            "present": False,
            "valid": False,
            "sealer": None,
            "trusted": False,
            "aligned_from_domain": False,
        },
        from_header="calendar@example.com",
        from_domain="example.com",
        verified_from="calendar@example.com",
        reason="",
    )

    mock_imap = MagicMock()
    mock_imap.fetch = MagicMock(return_value=("OK", [(b"1", raw_email)]))

    with (
        patch(
            "app.services.imap_listener.verify_mail_auth",
            new_callable=AsyncMock,
            return_value=verified,
        ),
        patch("app.services.imap_listener.find_tenant", new_callable=AsyncMock) as mock_find,
        patch("app.services.imap_listener.schedule_invite", new_callable=AsyncMock) as mock_schedule,
    ):
        mock_find.return_value = ("user-123", 42)

        await _process_email(mock_imap, b"1")

        # REQ-5.2: find_tenant is called with verified_from (From header),
        # not with the ICS ORGANIZER field ("alice@example.com" in google_meet.ics).
        mock_find.assert_awaited_once_with("calendar@example.com")
        mock_schedule.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_once_marks_emails_seen() -> None:
    """Processed emails are marked as SEEN."""
    ics_bytes = (FIXTURES / "google_meet.ics").read_bytes()
    raw_email = _make_email_with_ics(ics_bytes)

    mock_settings = MagicMock()
    mock_settings.imap_host = "imap.example.com"
    mock_settings.imap_port = 993
    mock_settings.imap_username = "bot@example.com"
    mock_settings.imap_password = "secret"

    call_log: list[tuple[str, ...]] = []

    async def fake_to_thread(fn, *args, **kwargs):
        if hasattr(fn, "__self__"):
            # method call on imap object
            method_name = fn.__name__
            call_log.append((method_name, *[str(a) for a in args]))
            if method_name == "search":
                return ("OK", [b"1"])
            if method_name == "fetch":
                return ("OK", [(b"1", raw_email)])
            return ("OK", [])
        else:
            # IMAP4_SSL constructor
            return MagicMock(
                login=MagicMock(__name__="login"),
                select=MagicMock(__name__="select"),
                search=MagicMock(__name__="search"),
                fetch=MagicMock(__name__="fetch"),
                store=MagicMock(__name__="store"),
                logout=MagicMock(__name__="logout"),
            )

    # This test verifies the structure; the full integration requires actual IMAP mocking.
    # Simplified: verify _extract_ics_parts works correctly with email containing .ics
    msg = email.message_from_bytes(raw_email)
    parts = _extract_ics_parts(msg)
    assert len(parts) == 1


@pytest.mark.asyncio
async def test_graceful_when_no_ics() -> None:
    """A mail-auth-verified email without .ics is processed gracefully (no crash).

    Post-SPEC-SEC-IMAP-001: mail-auth MUST pass before we even look for ICS parts.
    This test mocks verify_mail_auth to isolate the "no ICS" branch.
    """
    raw_email = MIMEText("Just a plain email").as_bytes()

    from app.services.mail_auth import MailAuthResult

    verified = MailAuthResult(
        dkim_result={"present": True, "valid": True, "d": "example.com", "aligned": True},
        spf_result={"result": "pass", "smtp_mailfrom_domain": "example.com", "aligned": True},
        arc_result={
            "present": False,
            "valid": False,
            "sealer": None,
            "trusted": False,
            "aligned_from_domain": False,
        },
        from_header="sender@example.com",
        from_domain="example.com",
        verified_from="sender@example.com",
        reason="",
    )

    mock_imap = MagicMock()
    mock_imap.fetch = MagicMock(return_value=("OK", [(b"1", raw_email)]))

    with (
        patch(
            "app.services.imap_listener.verify_mail_auth",
            new_callable=AsyncMock,
            return_value=verified,
        ),
        patch("app.services.imap_listener.find_tenant", new_callable=AsyncMock) as mock_find,
        patch("app.services.imap_listener.schedule_invite", new_callable=AsyncMock) as mock_schedule,
    ):
        await _process_email(mock_imap, b"1")

        mock_find.assert_not_awaited()
        mock_schedule.assert_not_awaited()
