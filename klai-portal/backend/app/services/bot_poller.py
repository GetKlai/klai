"""
Background task: poll Vexa for active meetings and trigger transcription when done.

Three responsibilities:
1. Reconcile Vexa's lifecycle state from GET /bots/status. An `active` meeting is
   the admission-backed signal that recording has started; container liveness alone is not.
2. Detect meetings whose bot disappeared and trigger transcription as a webhook backstop.
3. Recover meetings stuck in "stopping" — if the "completed" webhook from Vexa
   never arrives after stop_bot(), the meeting would stay in "stopping" forever.
   After PROCESSING_TIMEOUT_MINUTES, the poller tries to transcribe directly.

Session lifecycle rule (2026-04-24):
  Cross-org rows are read once per cycle inside `cross_org_session()` and
  SNAPSHOTTED into `_ActiveMeetingSnapshot` dataclasses. The rest of the loop
  operates on those primitives. Never touch an ORM attribute outside the
  owning session — rollback-on-exit expires all attributes and lazy access
  raises DetachedInstanceError (regression observed in production flooding
  portal-api logs every 10s).
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from app.api.meetings import ACTIVE_STATUSES, run_transcription
from app.core.database import cross_org_session, set_tenant, tenant_scoped_session
from app.models.meetings import VexaMeeting
from app.services.recording_cleanup import cleanup_recording
from app.services.vexa import MAX_WAIT_FOR_ADMISSION_MS, MeetingRef, parse_meeting_url, vexa

logger = structlog.get_logger()

POLL_INTERVAL = 10  # seconds — Vexa's own documented polling interval
PROCESSING_TIMEOUT_MINUTES = 10  # retry transcription after this many minutes stuck in "processing"
JOINING_CALLBACK_GRACE_SECONDS = 60
JOINING_TIMEOUT_SECONDS = MAX_WAIT_FOR_ADMISSION_MS // 1000 + JOINING_CALLBACK_GRACE_SECONDS
_PREACTIVE_VEXA_STATUSES = frozenset({"requested", "joining", "awaiting_admission"})
_VEXA_LIFECYCLE_STATUSES = _PREACTIVE_VEXA_STATUSES | {"active", "stopping", "completed", "failed"}


@dataclass(frozen=True)
class _ActiveMeetingSnapshot:
    """Immutable primitives captured from a VexaMeeting row inside the
    cross-org session. Safe to pass around after the session has closed.
    """

    id: uuid.UUID
    org_id: int | None
    meeting_url: str
    status: str
    started_at: datetime | None = None


def _snapshot(m: VexaMeeting) -> _ActiveMeetingSnapshot:
    return _ActiveMeetingSnapshot(
        id=m.id,
        org_id=m.org_id,
        meeting_url=m.meeting_url,
        status=m.status,
        started_at=m.started_at or m.created_at,
    )


async def _handle_meeting_ended(snap: _ActiveMeetingSnapshot) -> None:
    """Reconcile a bot that is no longer present in Vexa's non-terminal list.

    Tenant-scoped: uses snap.org_id to satisfy vexa_meetings UPDATE RLS.
    """
    logger.info("Bot poll: meeting ended, triggering transcription", meeting_id=str(snap.id))
    if snap.org_id is None:
        logger.warning("bot_poll_skipped_missing_org_id", meeting_id=str(snap.id))
        return
    async with tenant_scoped_session(snap.org_id) as db:
        m = await db.scalar(
            select(VexaMeeting).where(
                VexaMeeting.id == snap.id,
                VexaMeeting.status.in_(ACTIVE_STATUSES),
            )
        )
        if m is None:
            return  # webhook already handled it

        if m.status in {"pending", "joining"}:
            m.status = "failed"
            m.ended_at = m.ended_at or datetime.now(UTC)
            m.error_message = "Bot ended before recording was confirmed"
            await db.commit()
            return

        m.status = "stopping"
        m.ended_at = m.ended_at or datetime.now(UTC)
        await db.commit()

        await set_tenant(db, snap.org_id)
        await run_transcription(m)
        await db.commit()
        if m.status == "done":
            await cleanup_recording(m, db)


async def _fetch_running_keys_safe(
    active: list[_ActiveMeetingSnapshot],
) -> dict[tuple[str, str], str | None] | None:
    """Return each running Vexa meeting's admission-backed lifecycle status.

    Returns None if the Vexa API call fails (caller must skip end-detection that cycle).
    Legacy responses without a meeting lifecycle status retain the key with a None value:
    they prove liveness, but not admission.
    """
    if not active:
        return None
    try:
        running_bots = await vexa.get_running_bots()
        statuses: dict[tuple[str, str], str | None] = {}
        for bot in running_bots:
            raw_status = bot.get("meeting_status") or bot.get("status")
            lifecycle_status = raw_status if raw_status in _VEXA_LIFECYCLE_STATUSES else None
            statuses[(bot["platform"], bot["native_meeting_id"])] = lifecycle_status
        return statuses
    except Exception:
        logger.warning(
            "Bot status poll failed — skipping end detection this cycle",
            exc_info=True,
        )
        return None


async def _confirm_recording(snap: _ActiveMeetingSnapshot) -> None:
    """Promote joining only after Vexa reports its admitted `active` lifecycle state."""
    if snap.org_id is None:
        logger.warning("recording_confirmation_skipped_missing_org_id", meeting_id=str(snap.id))
        return
    async with tenant_scoped_session(snap.org_id) as db:
        meeting = await db.scalar(
            select(VexaMeeting).where(
                VexaMeeting.id == snap.id,
                VexaMeeting.status == "joining",
            )
        )
        if meeting is None:
            return
        meeting.status = "recording"
        await db.commit()
        logger.info("Bot poll: Vexa confirmed meeting active", meeting_id=str(snap.id))


def _joining_timed_out(snap: _ActiveMeetingSnapshot) -> bool:
    return bool(
        snap.status == "joining"
        and snap.started_at is not None
        and datetime.now(UTC) - snap.started_at >= timedelta(seconds=JOINING_TIMEOUT_SECONDS)
    )


async def _fail_joining_timeout(snap: _ActiveMeetingSnapshot, ref: MeetingRef) -> None:
    """Stop and fail a bot that stayed pre-active beyond its admission budget plus callback grace."""
    logger.warning(
        "Bot poll: meeting did not reach active before admission deadline",
        meeting_id=str(snap.id),
        timeout_seconds=JOINING_TIMEOUT_SECONDS,
    )
    try:
        await vexa.stop_bot(ref.platform, ref.native_meeting_id)
    except Exception:
        logger.warning(
            "Bot poll: failed to stop meeting after admission deadline",
            meeting_id=str(snap.id),
            exc_info=True,
        )
        return

    if snap.org_id is None:
        logger.warning("joining_timeout_skipped_missing_org_id", meeting_id=str(snap.id))
        return
    async with tenant_scoped_session(snap.org_id) as db:
        meeting = await db.scalar(
            select(VexaMeeting).where(
                VexaMeeting.id == snap.id,
                VexaMeeting.status == "joining",
            )
        )
        if meeting is None:
            return
        meeting.status = "failed"
        meeting.ended_at = meeting.ended_at or datetime.now(UTC)
        meeting.error_message = f"Bot was not admitted within {JOINING_TIMEOUT_SECONDS // 60} minutes"
        await db.commit()


async def _recover_stuck_meeting(snap: _ActiveMeetingSnapshot) -> None:
    """Force-transcribe a meeting that has been stuck in 'stopping' too long."""
    logger.warning(
        "Bot poll: meeting stuck in stopping",
        meeting_id=str(snap.id),
        timeout_minutes=PROCESSING_TIMEOUT_MINUTES,
    )
    if snap.org_id is None:
        logger.warning("stuck_meeting_skipped_missing_org_id", meeting_id=str(snap.id))
        return
    async with tenant_scoped_session(snap.org_id) as db:
        m = await db.scalar(
            select(VexaMeeting).where(
                VexaMeeting.id == snap.id,
                VexaMeeting.status == "stopping",
            )
        )
        if m is None:
            return
        await run_transcription(m)
        await db.commit()
        if m.status == "done":
            await cleanup_recording(m, db)


async def _load_cycle_snapshots() -> tuple[list[_ActiveMeetingSnapshot], list[_ActiveMeetingSnapshot]]:
    """Load active + stuck meetings in a cross-org pass and return primitives.

    Snapshotting happens INSIDE the session context so that session close
    cannot strip attributes out from under us.

    Platform-level poll: scans across every tenant via `cross_org_session`
    which sets `app.cross_org_admin=true`. The upgraded RLS policies on
    vexa_meetings honour that flag as an opt-in bypass. Per-meeting WRITES
    still run in `tenant_scoped_session(meeting.org_id)` so RLS enforces
    isolation on the mutations themselves.
    """
    # @MX:SPEC: SPEC-SEC-007
    async with cross_org_session() as db:
        active_result = await db.execute(select(VexaMeeting).where(VexaMeeting.status.in_(ACTIVE_STATUSES)))
        active = [_snapshot(m) for m in active_result.scalars().all()]

        timeout_cutoff = datetime.now(UTC) - timedelta(minutes=PROCESSING_TIMEOUT_MINUTES)
        stuck_result = await db.execute(_stuck_meetings_stmt(timeout_cutoff))
        stuck = [_snapshot(m) for m in stuck_result.scalars().all()]

    return active, stuck


def _stuck_meetings_stmt(timeout_cutoff: datetime):
    return select(VexaMeeting).where(
        VexaMeeting.status == "stopping",
        (
            (VexaMeeting.ended_at.is_not(None) & (VexaMeeting.ended_at < timeout_cutoff))
            | (VexaMeeting.ended_at.is_(None) & (VexaMeeting.created_at < timeout_cutoff))
        ),
    )


async def _poll_once() -> None:
    """Execute a single poll cycle. Extracted for testability — `poll_loop`
    is just this function wrapped in an infinite retry loop.
    """
    active, stuck = await _load_cycle_snapshots()

    # Fetch running bots once per cycle. Presence proves workload liveness;
    # only the Vexa lifecycle state `active` proves meeting admission.
    running_statuses = await _fetch_running_keys_safe(active)

    for snap in active:
        if running_statuses is None:
            continue  # poll failed; don't trigger transcription based on missing data

        ref = parse_meeting_url(snap.meeting_url)
        if ref is None:
            continue

        key = (ref.platform, ref.native_meeting_id)
        if key not in running_statuses:
            await _handle_meeting_ended(snap)
            continue

        vexa_status = running_statuses[key]
        if snap.status == "joining" and vexa_status == "active":
            await _confirm_recording(snap)
        elif _joining_timed_out(snap):
            await _fail_joining_timeout(snap, ref)

    for snap in stuck:
        await _recover_stuck_meeting(snap)


async def poll_loop() -> None:
    """Async task: run forever, polling Vexa for active meetings."""
    await asyncio.sleep(15)  # let the app finish starting up
    while True:
        try:
            await _poll_once()
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Bot poll loop: unexpected error")

        await asyncio.sleep(POLL_INTERVAL)
