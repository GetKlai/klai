"""
Background task: poll Vexa for active meetings and trigger transcription when done.

Two responsibilities:
1. Detect meetings that ended naturally (host closed Google Meet) — Vexa doesn't
   send a webhook in that case. Polls GET /bots/status every POLL_INTERVAL seconds
   and triggers transcription when a meeting's native_meeting_id is no longer present.
2. Recover meetings stuck in "stopping" — if the "completed" webhook from Vexa
   never arrives after stop_bot(), the meeting would stay in "stopping" forever.
   After PROCESSING_TIMEOUT_MINUTES, the poller tries to transcribe directly.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from app.api.meetings import ACTIVE_STATUSES, run_transcription
from app.core.database import cross_org_session, tenant_scoped_session
from app.models.meetings import VexaMeeting
from app.services.recording_cleanup import cleanup_recording
from app.services.vexa import parse_meeting_url, vexa

logger = structlog.get_logger()

POLL_INTERVAL = 10  # seconds — Vexa's own documented polling interval
PROCESSING_TIMEOUT_MINUTES = 10  # retry transcription after this many minutes stuck in "processing"


async def _upgrade_joining_to_recording(meeting_id: object, org_id: int) -> None:
    """Set meeting status joining→recording when the bot is confirmed active.

    Runs in a tenant-scoped session so vexa_meetings' UPDATE RLS policy
    accepts the write. Caller holds the meeting object and provides org_id.
    """
    async with tenant_scoped_session(org_id) as db:
        m = await db.scalar(
            select(VexaMeeting).where(
                VexaMeeting.id == meeting_id,
                VexaMeeting.status == "joining",
            )
        )
        if m is not None:
            m.status = "recording"
            await db.commit()
            logger.info("Bot poll: bot active, updated status joining→recording", meeting_id=str(meeting_id))


async def _handle_meeting_ended(meeting: VexaMeeting) -> None:
    """Bot is gone from Vexa — transition meeting to stopping and run transcription.

    Tenant-scoped: uses meeting.org_id to satisfy vexa_meetings UPDATE RLS.
    """
    logger.info("Bot poll: meeting ended, triggering transcription", meeting_id=str(meeting.id))
    if meeting.org_id is None:
        logger.warning("bot_poll_skipped_missing_org_id", meeting_id=str(meeting.id))
        return
    async with tenant_scoped_session(meeting.org_id) as db:
        m = await db.scalar(
            select(VexaMeeting).where(
                VexaMeeting.id == meeting.id,
                VexaMeeting.status.in_(ACTIVE_STATUSES),
            )
        )
        if m is None:
            return  # webhook already handled it

        m.status = "stopping"
        m.ended_at = m.ended_at or datetime.now(UTC)
        await db.commit()

        await run_transcription(m, db)
        await db.commit()
        if m.status == "done":
            await cleanup_recording(m, db)


async def _fetch_running_keys_safe(active: list[VexaMeeting]) -> set[tuple[str, str]] | None:
    """Return the set of (platform, native_meeting_id) for all running Vexa bots.

    Returns None if the Vexa API call fails (caller must skip end-detection that cycle).
    """
    if not active:
        return None
    try:
        running_bots = await vexa.get_running_bots()
        return {(b["platform"], b["native_meeting_id"]) for b in running_bots}
    except Exception as exc:
        logger.warning(
            "Bot status poll failed — skipping end detection this cycle",
            error=str(exc),
            exc_info=True,
        )
        return None


async def _recover_stuck_meeting(meeting: VexaMeeting) -> None:
    """Force-transcribe a meeting that has been stuck in 'stopping' too long."""
    logger.warning(
        "Bot poll: meeting stuck in stopping",
        meeting_id=str(meeting.id),
        timeout_minutes=PROCESSING_TIMEOUT_MINUTES,
    )
    if meeting.org_id is None:
        logger.warning("stuck_meeting_skipped_missing_org_id", meeting_id=str(meeting.id))
        return
    async with tenant_scoped_session(meeting.org_id) as db:
        m = await db.scalar(
            select(VexaMeeting).where(
                VexaMeeting.id == meeting.id,
                VexaMeeting.status == "stopping",
            )
        )
        if m is None:
            return
        await run_transcription(m, db)
        await db.commit()
        if m.status == "done":
            await cleanup_recording(m, db)


async def poll_loop() -> None:
    """Async task: run forever, polling Vexa for active meetings."""
    await asyncio.sleep(15)  # let the app finish starting up
    while True:
        try:
            # Platform-level poll: scan active and stuck meetings across every
            # tenant. Uses `cross_org_session` which explicitly sets
            # `app.cross_org_admin=true` — the upgraded RLS policies on
            # vexa_meetings honour that flag as an opt-in bypass. Per-meeting
            # WRITES below run in `tenant_scoped_session(meeting.org_id)` so
            # RLS still enforces isolation on the mutations themselves.
            # @MX:SPEC: SPEC-SEC-007
            async with cross_org_session() as db:
                result = await db.execute(select(VexaMeeting).where(VexaMeeting.status.in_(ACTIVE_STATUSES)))
                active = list(result.scalars().all())

                timeout_cutoff = datetime.now(UTC) - timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)
                stuck_result = await db.execute(
                    select(VexaMeeting).where(
                        VexaMeeting.status == "stopping",
                        VexaMeeting.ended_at < timeout_cutoff,
                        VexaMeeting.vexa_meeting_id.is_not(None),
                    )
                )
                stuck = list(stuck_result.scalars().all())

            # Fetch running bots once per cycle — a meeting has ended when its
            # (platform, native_meeting_id) is absent from this list.
            running_keys = await _fetch_running_keys_safe(active)

            for meeting in active:
                if running_keys is None:
                    continue  # poll failed; don't trigger transcription based on missing data

                ref = parse_meeting_url(meeting.meeting_url)
                if ref is None:
                    continue

                if (ref.platform, ref.native_meeting_id) in running_keys:
                    if meeting.status == "joining" and meeting.org_id is not None:
                        await _upgrade_joining_to_recording(meeting.id, meeting.org_id)
                    continue  # bot is still running

                await _handle_meeting_ended(meeting)

            for meeting in stuck:
                await _recover_stuck_meeting(meeting)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Bot poll loop: unexpected error")

        await asyncio.sleep(POLL_INTERVAL)
