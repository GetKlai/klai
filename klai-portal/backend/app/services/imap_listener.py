"""IMAP listener — poll an IMAP mailbox for calendar invites.

Connects to IMAP4_SSL, polls for UNSEEN emails, verifies sender identity
via DKIM/SPF/ARC (SPEC-SEC-IMAP-001), extracts .ics data, parses invites,
resolves tenants, and schedules bot joins.
"""

import asyncio
import imaplib
from email.message import Message

import structlog

from app.core.config import settings
from app.services.ical_parser import parse_ics
from app.services.invite_scheduler import schedule_invite
from app.services.mail_auth import result_log_fields, verify_mail_auth
from app.services.tenant_matcher import find_tenant

logger = structlog.get_logger()

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
            logger.exception("imap_poll_error", backoff_seconds=backoff)
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
        logger.info("imap_poll_found_unseen", count=len(msg_ids))

        for msg_id in msg_ids:
            try:
                await _process_email(imap, msg_id)
            except Exception:
                logger.exception("imap_process_email_failed", imap_msg_id=msg_id.decode(errors="replace"))
            # Mark as SEEN regardless (avoid reprocessing). REQ-5.4: rejected
            # messages also get the \Seen flag so they do not loop.
            await asyncio.to_thread(imap.store, msg_id, "+FLAGS", "\\Seen")
    finally:
        try:
            await asyncio.to_thread(imap.logout)
        except Exception:
            logger.debug("imap_logout_failed", exc_info=True)


async def _process_email(imap: imaplib.IMAP4_SSL, msg_id: bytes) -> None:
    """Extract .ics content from a single email and process it.

    SPEC-SEC-IMAP-001: gates every downstream call (parse_ics, find_tenant,
    schedule_invite) on verify_mail_auth. Messages whose RFC-5322 From
    identity cannot be cryptographically verified are logged as
    ``imap_auth_failed`` and dropped before any tenant lookup.
    """
    _status, msg_data = await asyncio.to_thread(imap.fetch, msg_id.decode(), "(RFC822)")
    if not msg_data or not msg_data[0]:
        return

    raw_email = msg_data[0]
    if not isinstance(raw_email, tuple):
        return
    raw_bytes = raw_email[1]

    # REQ-1..REQ-5: verify mail-auth. The result carries the parsed Message
    # so we don't re-parse raw_bytes downstream.
    auth = await verify_mail_auth(raw_bytes)

    if auth.verified_from is None:
        # REQ-4.1 + REQ-4.2: stable log keys; no body, no ICS payload.
        logger.warning(
            "imap_auth_failed",
            reason=auth.reason,
            from_header=auth.from_header,
            from_domain=auth.from_domain,
            message_id=auth.message_id,
            **result_log_fields(auth),
        )
        return

    # REQ-4.3: positive trail for post-incident forensics.
    logger.info(
        "imap_auth_passed",
        verified_from=auth.verified_from,
        from_domain=auth.from_domain,
        message_id=auth.message_id,
        **result_log_fields(auth),
    )

    # verified_from is not None implies the raw bytes were parseable; the
    # invariant is documented on MailAuthResult.
    assert auth.parsed_message is not None
    ics_parts = _extract_ics_parts(auth.parsed_message)
    if not ics_parts:
        logger.debug("imap_no_ics_content", message_id=auth.message_id)
        return

    for ics_bytes in ics_parts:
        invite = parse_ics(ics_bytes)
        if invite is None:
            continue

        # REQ-5.3: audit ICS/From organizer mismatches but do not reject.
        # A minority of calendar clients put a delegated organizer in the
        # ICS while the message is sent from the delegate's mailbox; we
        # must see these but not break them. mail-auth is authoritative,
        # the ICS field is informational.
        if invite.organizer_email and invite.organizer_email.lower() != auth.verified_from:
            logger.warning(
                "imap_organizer_mismatch",
                verified_from=auth.verified_from,
                ics_organizer=invite.organizer_email,
                message_id=auth.message_id,
            )

        # REQ-5.2: find_tenant is called with verified_from, NOT
        # invite.organizer_email. The ICS ORGANIZER field is
        # attacker-controlled; only the DKIM-verified RFC-5322 From
        # header is authoritative.
        tenant = await find_tenant(auth.verified_from)
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
