"""
Background task: poll Vexa for active meetings and trigger transcription when done.

The vexa webhook fires when the user clicks Stop, but not when the Google Meet
host ends the call. This poller detects that case by calling get_bot_status()
every POLL_INTERVAL seconds. A 404 or a non-active status means the bot has
left the meeting and we should transcribe the recording.
"""

import asyncio
import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.api.meetings import ACTIVE_STATUSES, _run_transcription
from app.core.database import AsyncSessionLocal
from app.models.meetings import VexaMeeting
from app.services.vexa import parse_meeting_url, vexa

logger = logging.getLogger(__name__)

# Bot-manager statuses that mean the bot is still active in the call
_BOT_ACTIVE = {"joining", "in_call_recording", "recording", "waiting", "starting", "pending"}
POLL_INTERVAL = 30  # seconds


async def _bot_ended(meeting: VexaMeeting) -> bool:
    """Return True if the Vexa bot has left this meeting."""
    ref = parse_meeting_url(meeting.meeting_url)
    if ref is None:
        return False
    try:
        resp = await vexa.get_bot_status(ref.platform, ref.native_meeting_id)
        bot_status = resp.get("status", "")
        return bool(bot_status) and bot_status not in _BOT_ACTIVE
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return True  # bot session is gone — meeting ended
        logger.warning("Bot status check failed for meeting %s: %s", meeting.id, exc)
    except Exception as exc:
        logger.warning("Bot status check error for meeting %s: %s", meeting.id, exc)
    return False


async def poll_loop() -> None:
    """Async task: run forever, polling Vexa for active meetings."""
    await asyncio.sleep(15)  # let the app finish starting up
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(VexaMeeting).where(VexaMeeting.status.in_(ACTIVE_STATUSES)))
                active = list(result.scalars().all())

            for meeting in active:
                if not await _bot_ended(meeting):
                    continue

                logger.info("Bot poll: meeting %s ended — triggering transcription", meeting.id)
                async with AsyncSessionLocal() as db:
                    # Re-fetch with status guard to prevent race with the webhook
                    m = await db.scalar(
                        select(VexaMeeting).where(
                            VexaMeeting.id == meeting.id,
                            VexaMeeting.status.in_(ACTIVE_STATUSES),
                        )
                    )
                    if m is None:
                        continue  # webhook already handled it

                    m.status = "processing"
                    m.ended_at = m.ended_at or datetime.now(UTC)
                    await db.commit()
                    await db.refresh(m)

                    await _run_transcription(m, db)
                    await db.commit()

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Bot poll loop: unexpected error")

        await asyncio.sleep(POLL_INTERVAL)
