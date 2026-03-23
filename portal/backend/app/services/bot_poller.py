"""
Background task: poll Vexa for active meetings and trigger transcription when done.

Two responsibilities:
1. Detect meetings that ended naturally (host closed Google Meet) — Vexa doesn't
   send a webhook in that case. Detects via get_bot_status() every POLL_INTERVAL.
2. Recover meetings stuck in "processing" — if the "completed" webhook from Vexa
   never arrives after stop_bot(), the meeting would stay in "processing" forever.
   After PROCESSING_TIMEOUT_MINUTES, the poller tries to transcribe directly.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select

from app.api.meetings import ACTIVE_STATUSES, run_transcription
from app.core.database import AsyncSessionLocal
from app.models.meetings import VexaMeeting
from app.services.vexa import parse_meeting_url, vexa

logger = logging.getLogger(__name__)

# Vexa meeting statuses that mean the bot is still active / in progress
_BOT_ACTIVE = {"requested", "joining", "awaiting_admission", "active", "stopping"}
POLL_INTERVAL = 30  # seconds
PROCESSING_TIMEOUT_MINUTES = 10  # retry transcription after this many minutes stuck in "processing"


async def _bot_ended(meeting: VexaMeeting) -> tuple[bool, int | None]:
    """Return (ended, vexa_meeting_id) — ended=True if the Vexa bot has left this meeting."""
    ref = parse_meeting_url(meeting.meeting_url)
    if ref is None:
        return False, None
    try:
        resp = await vexa.get_bot_status(ref.platform, ref.native_meeting_id)
        bot_status = resp.get("status", "")
        vexa_id = resp.get("id")
        ended = bool(bot_status) and bot_status not in _BOT_ACTIVE
        return ended, vexa_id if ended else None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return True, None  # bot session is gone — meeting ended
        logger.warning("Bot status check failed for meeting %s: %s", meeting.id, exc)
    except Exception as exc:
        logger.warning("Bot status check error for meeting %s: %s", meeting.id, exc)
    return False, None


async def poll_loop() -> None:
    """Async task: run forever, polling Vexa for active meetings."""
    await asyncio.sleep(15)  # let the app finish starting up
    while True:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(VexaMeeting).where(VexaMeeting.status.in_(ACTIVE_STATUSES)))
                active = list(result.scalars().all())

                # Also pick up meetings stuck in "processing" (webhook never arrived)
                timeout_cutoff = datetime.now(UTC) - timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)
                stuck_result = await db.execute(
                    select(VexaMeeting).where(
                        VexaMeeting.status == "processing",
                        VexaMeeting.ended_at < timeout_cutoff,
                        VexaMeeting.vexa_meeting_id.is_not(None),
                    )
                )
                stuck = list(stuck_result.scalars().all())

            for meeting in active:
                ended, vexa_meeting_id = await _bot_ended(meeting)
                if not ended:
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
                    if vexa_meeting_id and not m.vexa_meeting_id:
                        m.vexa_meeting_id = vexa_meeting_id
                    await db.commit()
                    await db.refresh(m)

                    await run_transcription(m, db)
                    await db.commit()

            for meeting in stuck:
                logger.warning(
                    "Bot poll: meeting %s stuck in processing for >%d min — retrying transcription",
                    meeting.id,
                    PROCESSING_TIMEOUT_MINUTES,
                )
                async with AsyncSessionLocal() as db:
                    m = await db.scalar(
                        select(VexaMeeting).where(
                            VexaMeeting.id == meeting.id,
                            VexaMeeting.status == "processing",
                        )
                    )
                    if m is None:
                        continue
                    await run_transcription(m, db)
                    await db.commit()

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Bot poll loop: unexpected error")

        await asyncio.sleep(POLL_INTERVAL)
