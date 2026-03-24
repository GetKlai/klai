"""
IMAP listener -- poll an IMAP mailbox for calendar invites.

Connects to IMAP4_SSL, polls for UNSEEN emails, extracts .ics data,
parses invites, resolves tenants, and schedules bot joins.
"""

import asyncio
import email
import imaplib
import logging
from email.message import Message

from app.core.config import settings
from app.services.ical_parser import parse_ics
from app.services.invite_scheduler import schedule_invite
from app.services.tenant_matcher import find_tenant

logger = logging.getLogger(__name__)

# Exponential backoff limits
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 60.0


async def start_imap_listener() -> None:
    """Main loop: poll IMAP for calendar invites. Runs as an asyncio task."""
    await asyncio.sleep(15)  # let app finish starting
    backoff = _BACKOFF_BASE

    while True:
        try:
            await _poll_once()
            backoff = _BACKOFF_BASE  # reset on success
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("IMAP poll error (retrying in %.0fs)", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
            continue

        await asyncio.sleep(settings.imap_poll_interval_seconds)


async def _poll_once() -> None:
    """Connect to IMAP, fetch UNSEEN emails, process calendar parts."""
    assert settings.imap_host and settings.imap_username and settings.imap_password

    # imaplib is blocking -- run in a thread
    imap = await asyncio.to_thread(imaplib.IMAP4_SSL, settings.imap_host, settings.imap_port)
    try:
        await asyncio.to_thread(imap.login, settings.imap_username, settings.imap_password)
        await asyncio.to_thread(imap.select, "INBOX")

        _status, data = await asyncio.to_thread(imap.search, None, "UNSEEN")
        if not data or not data[0]:
            return

        msg_ids = data[0].split()
        logger.info("IMAP: found %d unseen emails", len(msg_ids))

        for msg_id in msg_ids:
            try:
                await _process_email(imap, msg_id)
            except Exception:
                logger.exception("Failed to process email %s", msg_id)
            # Mark as SEEN regardless (avoid reprocessing)
            await asyncio.to_thread(imap.store, msg_id, "+FLAGS", "\\Seen")
    finally:
        try:
            await asyncio.to_thread(imap.logout)
        except Exception:
            logger.debug("IMAP logout failed (connection may already be closed)")


async def _process_email(imap: imaplib.IMAP4_SSL, msg_id: bytes) -> None:
    """Extract .ics content from a single email and process it."""
    _status, msg_data = await asyncio.to_thread(imap.fetch, msg_id.decode(), "(RFC822)")
    if not msg_data or not msg_data[0]:
        return

    raw_email = msg_data[0]
    if isinstance(raw_email, tuple):
        raw_bytes = raw_email[1]
    else:
        return

    msg = email.message_from_bytes(raw_bytes)
    ics_parts = _extract_ics_parts(msg)

    if not ics_parts:
        logger.debug("No iCal content in email %s", msg_id)
        return

    # Process each .ics part (usually just one)
    for ics_bytes in ics_parts:
        invite = parse_ics(ics_bytes)
        if invite is None:
            continue

        tenant = await find_tenant(invite.organizer_email)
        if tenant is None:
            continue

        zitadel_user_id, org_id = tenant
        await schedule_invite(invite, zitadel_user_id, org_id)


def _extract_ics_parts(msg: Message) -> list[bytes]:
    """Extract text/calendar MIME parts and .ics attachments from an email."""
    parts: list[bytes] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            filename = part.get_filename() or ""

            if content_type == "text/calendar":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    parts.append(payload)
            elif filename.lower().endswith(".ics"):
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    parts.append(payload)
    else:
        content_type = msg.get_content_type()
        if content_type == "text/calendar":
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                parts.append(payload)

    return parts
