"""
Invite scheduler -- schedule bot joins for calendar invites.

Manages an in-memory dict of asyncio.Task objects keyed by iCal UID.
Each task sleeps until DTSTART - 60s, then creates a VexaMeeting and
dispatches a bot via the VexaClient.

Includes rate limiting (AC-14b): max N invite-triggered bot joins per user per UTC day.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.meetings import VexaMeeting
from app.services.ical_parser import ParsedInvite
from app.services.vexa import vexa

logger = logging.getLogger(__name__)

# In-memory registry of scheduled tasks by iCal UID
_scheduled: dict[str, asyncio.Task[None]] = {}

# How many seconds before DTSTART to join the meeting
JOIN_LEAD_SECONDS = 60


async def _check_rate_limit(zitadel_user_id: str, db: AsyncSession) -> bool:
    """Return True if the user has exceeded the daily invite rate limit (AC-14b)."""
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    count = await db.scalar(
        select(func.count())
        .select_from(VexaMeeting)
        .where(
            VexaMeeting.zitadel_user_id == zitadel_user_id,
            VexaMeeting.ical_uid.isnot(None),
            VexaMeeting.created_at >= today_start,
        )
    )
    return (count or 0) >= settings.invite_bot_rate_limit_per_user_per_day


async def schedule_invite(invite: ParsedInvite, zitadel_user_id: str, org_id: int | None) -> None:
    """Schedule a bot join for the given invite, or handle cancellation."""
    if invite.is_cancellation:
        await cancel_invite(invite.uid)
        return

    now = datetime.now(UTC)
    join_at = invite.dtstart.timestamp() - JOIN_LEAD_SECONDS
    delay = join_at - now.timestamp()

    if delay < 0:
        logger.info("Ignoring past/imminent meeting: uid=%s dtstart=%s", invite.uid, invite.dtstart)
        return

    # Check DB for existing ical_uid (dedup) and rate limit
    async with AsyncSessionLocal() as db:
        existing = await db.scalar(select(VexaMeeting.id).where(VexaMeeting.ical_uid == invite.uid))
        if existing is not None:
            logger.info("Duplicate invite ignored: uid=%s already in DB", invite.uid)
            return

        # AC-14b: rate limit check
        if await _check_rate_limit(zitadel_user_id, db):
            logger.warning(
                "Rate limit exceeded for user %s (limit=%d/day)",
                zitadel_user_id,
                settings.invite_bot_rate_limit_per_user_per_day,
            )
            return

    # Cancel existing task for this UID if any (updated invite)
    if invite.uid in _scheduled:
        _scheduled[invite.uid].cancel()

    task = asyncio.create_task(_join_meeting(invite, zitadel_user_id, org_id, delay))
    _scheduled[invite.uid] = task
    logger.info("Scheduled bot join for uid=%s in %.0fs", invite.uid, delay)


async def cancel_invite(uid: str) -> None:
    """Cancel a scheduled invite and update/stop the meeting if needed."""
    # Cancel the asyncio task if still pending
    task = _scheduled.pop(uid, None)
    if task is not None and not task.done():
        task.cancel()
        logger.info("Cancelled scheduled task for uid=%s", uid)

    # Update DB record if exists
    async with AsyncSessionLocal() as db:
        meeting = await db.scalar(select(VexaMeeting).where(VexaMeeting.ical_uid == uid))
        if meeting is not None:
            if meeting.status == "in_progress":
                # Bot is already in the meeting -- stop it
                try:
                    await vexa.stop_bot(meeting.platform, meeting.native_meeting_id)
                    logger.info("Stopped active bot for cancelled meeting uid=%s", uid)
                except Exception:
                    logger.exception("Failed to stop bot for cancelled meeting uid=%s", uid)
            meeting.status = "cancelled"
            await db.commit()
            logger.info("Marked meeting as cancelled: uid=%s", uid)


async def _join_meeting(invite: ParsedInvite, zitadel_user_id: str, org_id: int | None, delay: float) -> None:
    """Wait until join time, then create VexaMeeting and start bot."""
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return

    try:
        async with AsyncSessionLocal() as db:
            # Final dedup check
            existing = await db.scalar(select(VexaMeeting.id).where(VexaMeeting.ical_uid == invite.uid))
            if existing is not None:
                logger.info("Meeting already exists at join time: uid=%s", invite.uid)
                return

            meeting = VexaMeeting(
                zitadel_user_id=zitadel_user_id,
                org_id=org_id,
                platform=invite.platform,
                native_meeting_id=invite.native_meeting_id,
                meeting_url=invite.meeting_url,
                meeting_title=invite.summary or None,
                status="pending",
                consent_given=True,
                ical_uid=invite.uid,
                started_at=datetime.now(UTC),
            )
            db.add(meeting)
            await db.commit()
            await db.refresh(meeting)

            try:
                bot_resp = await vexa.start_bot(invite.platform, invite.native_meeting_id)
                meeting.bot_id = bot_resp.get("bot_id") or bot_resp.get("id")
                meeting.status = "in_progress"
                logger.info("Bot started for meeting uid=%s bot_id=%s", invite.uid, meeting.bot_id)
            except Exception as exc:
                meeting.status = "error"
                meeting.error_message = str(exc)
                logger.exception("Failed to start bot for meeting uid=%s", invite.uid)

            await db.commit()
    except Exception:
        logger.exception("Unexpected error joining meeting uid=%s", invite.uid)
    finally:
        _scheduled.pop(invite.uid, None)


def get_scheduled() -> dict[str, asyncio.Task[None]]:
    """Return the scheduled tasks dict (for testing)."""
    return _scheduled
