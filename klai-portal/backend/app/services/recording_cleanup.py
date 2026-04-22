"""
Recording cleanup service -- delete Vexa recordings after successful transcription.

GDPR Article 5(1)(c) data minimisation: recordings are deleted as soon as
transcription completes. Uses Vexa meeting-api DELETE /recordings/{recording_id}.

SPEC: SPEC-GDPR-002 (R1-R7), SPEC-VEXA-001
"""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import cross_org_session, tenant_scoped_session
from app.models.meetings import VexaMeeting
from app.services.vexa import vexa

logger = structlog.get_logger()

CLEANUP_INTERVAL = 300  # seconds (5 minutes)
CLEANUP_AGE_MINUTES = 30  # only clean up recordings older than this


async def delete_recording(recording_id: int, meeting_id: str | None = None) -> bool:
    """Delete recording files via Vexa meeting-api.

    Returns True on success, False on failure. Never raises.
    SPEC: SPEC-GDPR-002-R2, R4, R6
    """
    try:
        success = await vexa.delete_recording(recording_id)
        if success:
            logger.info("Recording deleted via API", recording_id=recording_id, meeting_id=meeting_id)
        else:
            logger.warning("Recording deletion failed", recording_id=recording_id, meeting_id=meeting_id)
        return success
    except Exception as exc:
        logger.warning(
            "Recording cleanup error", recording_id=recording_id, meeting_id=meeting_id, error=str(exc), exc_info=True
        )
        return False


# @MX:ANCHOR fan_in=4 — invoked from meetings.vexa_webhook, meetings.delete_meeting,
#   bot_poller (_handle_meeting_ended, _recover_stuck_meeting), and the cleanup loop.
# @MX:REASON opens its OWN tenant_scoped_session for the UPDATE regardless of the
#   caller's session state — critical because callers (cleanup loop, webhook) may
#   hold cross-org or unscoped sessions. Changing that to reuse caller's `db` would
#   re-introduce the silent-filter regression fixed on 2026-04-22.
# @MX:SPEC SPEC-GDPR-002
async def cleanup_recording(
    meeting: VexaMeeting,
    db: AsyncSession,
    *,
    recording_id: int | None = None,
) -> None:
    """Attempt to delete the recording for a completed meeting and update the DB.

    Guards:
    - meeting.status must be "done"
    - meeting.vexa_meeting_id must be set (used as recording_id fallback)
    - meeting.recording_deleted must be False

    The recording_id parameter is preferred (from webhook payload).
    Falls back to vexa_meeting_id if not provided.

    Important: vexa_meetings has a tenant-scoped UPDATE RLS policy. The
    cleanup LOOP runs cross-org (no tenant context), so this function opens
    a `tenant_scoped_session` on the meeting's own org_id to perform the
    UPDATE. Without this, the UPDATE is silently filtered to 0 rows and
    recording_deleted never flips to True — the loop would then re-enqueue
    the same recording forever.

    SPEC: SPEC-GDPR-002-R1, R4, SPEC-VEXA-001
    """
    if meeting.status != "done":
        return
    if meeting.recording_deleted:
        return

    rid = recording_id or meeting.vexa_meeting_id
    if rid is None:
        return

    success = await delete_recording(rid, str(meeting.id))
    if not success:
        return
    if meeting.org_id is None:
        # Legacy meetings pre-SPEC-SEC-007 may lack org_id. Surface it —
        # otherwise the UPDATE below would be cross-org and RLS would
        # silently filter.
        logger.warning(
            "recording_cleanup_skipped_missing_org_id",
            meeting_id=str(meeting.id),
        )
        return

    # Scope the UPDATE to the meeting's own tenant context so RLS accepts it.
    # Using a SQL `update(...)` instead of `meeting.recording_deleted = True`
    # lets us run it on a fresh pinned session instead of the cross-org one.
    async with tenant_scoped_session(meeting.org_id) as scoped_db:
        result = await scoped_db.execute(
            update(VexaMeeting)
            .where(VexaMeeting.id == meeting.id)
            .values(recording_deleted=True, recording_deleted_at=datetime.now(UTC))
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise RuntimeError(
                f"vexa_meetings UPDATE matched 0 rows "
                f"(meeting_id={meeting.id}, org_id={meeting.org_id}) — "
                f"RLS/tenant-context mismatch"
            )
        await scoped_db.commit()
    # Reflect the change on the caller's stale in-memory object so subsequent
    # guards (meeting.recording_deleted) are accurate.
    meeting.recording_deleted = True
    meeting.recording_deleted_at = datetime.now(UTC)


async def recording_cleanup_loop() -> None:
    """Background task: periodically clean up recordings for done meetings.

    Runs every CLEANUP_INTERVAL seconds. Only targets meetings older than
    CLEANUP_AGE_MINUTES that still have recording_deleted=False.

    SPEC: SPEC-GDPR-002-R5
    """
    await asyncio.sleep(60)  # let the app finish starting
    while True:
        try:
            # Cross-org scan: the cleanup loop sees stale recordings across
            # every tenant in one pass. Uses `cross_org_session` (explicit
            # app.cross_org_admin bypass) so the SELECT is not blocked by
            # vexa_meetings' tenant-scoped RLS policy. The per-meeting UPDATE
            # still runs inside `tenant_scoped_session(meeting.org_id)` (see
            # cleanup_recording), so isolation is preserved for mutations.
            async with cross_org_session() as db:
                cutoff = datetime.now(UTC) - timedelta(minutes=CLEANUP_AGE_MINUTES)
                result = await db.execute(
                    select(VexaMeeting).where(
                        VexaMeeting.status == "done",
                        VexaMeeting.recording_deleted.is_(False),
                        VexaMeeting.vexa_meeting_id.is_not(None),
                        VexaMeeting.created_at < cutoff,
                    )
                )
                stale = list(result.scalars().all())

                for meeting in stale:
                    try:
                        await cleanup_recording(meeting, db)
                    except Exception as exc:
                        logger.warning(
                            "Recording cleanup loop error", meeting_id=str(meeting.id), error=str(exc), exc_info=True
                        )

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Recording cleanup loop: unexpected error")

        await asyncio.sleep(CLEANUP_INTERVAL)
