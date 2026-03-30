"""
Recording cleanup service -- delete Vexa recordings after successful transcription.

GDPR Article 5(1)(c) data minimisation: recordings are deleted as soon as
transcription completes.  The vexa-bot-manager container stores recordings in
ephemeral storage at /var/lib/vexa/recordings/{vexa_meeting_id}.  This module
uses Docker exec to remove them.

SPEC: SPEC-GDPR-002 (R1-R7)
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import docker
import docker.errors
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.meetings import VexaMeeting

logger = logging.getLogger(__name__)

RECORDINGS_BASE_PATH = "/var/lib/vexa/recordings"
CLEANUP_INTERVAL = 300  # seconds (5 minutes)
CLEANUP_AGE_MINUTES = 30  # only clean up recordings older than this


def _sync_delete_recording(vexa_meeting_id: int) -> tuple[int, str]:
    """Synchronous Docker exec to remove recording files.

    Returns (exit_code, output).  Runs in a thread via asyncio.to_thread().
    """
    client = docker.from_env()
    container = client.containers.get(settings.vexa_bot_container_name)
    exit_code, output = container.exec_run(
        ["rm", "-rf", f"{RECORDINGS_BASE_PATH}/{vexa_meeting_id}"],
        stdout=True,
        stderr=True,
    )
    return exit_code, output.decode() if isinstance(output, bytes) else str(output)


async def delete_recording(vexa_meeting_id: int, meeting_id: str | None = None) -> bool:
    """Delete recording files for a Vexa meeting from the bot-manager container.

    Returns True on success (including when files were already gone), False on failure.
    Never raises -- all errors are logged and swallowed.

    SPEC: SPEC-GDPR-002-R2, R4, R6
    """
    try:
        exit_code, output = await asyncio.to_thread(_sync_delete_recording, vexa_meeting_id)
        if exit_code == 0:
            logger.info(
                "Recording cleanup",
                extra={
                    "vexa_meeting_id": vexa_meeting_id,
                    "meeting_id": meeting_id,
                    "result": "deleted",
                },
            )
            return True

        logger.warning(
            "Recording cleanup: rm failed (exit %d): %s",
            exit_code,
            output.strip(),
            extra={
                "vexa_meeting_id": vexa_meeting_id,
                "meeting_id": meeting_id,
                "result": "failed",
                "error": output.strip(),
            },
        )
        return False

    except docker.errors.NotFound:
        logger.warning(
            "Recording cleanup: container not found",
            extra={
                "vexa_meeting_id": vexa_meeting_id,
                "meeting_id": meeting_id,
                "result": "failed",
                "error": f"Container {settings.vexa_bot_container_name} not found",
            },
        )
        return False
    except Exception as exc:
        logger.warning(
            "Recording cleanup: unexpected error: %s",
            exc,
            extra={
                "vexa_meeting_id": vexa_meeting_id,
                "meeting_id": meeting_id,
                "result": "failed",
                "error": str(exc),
            },
        )
        return False


async def cleanup_recording(meeting: VexaMeeting, db: AsyncSession) -> None:
    """Attempt to delete the recording for a completed meeting and update the DB.

    Guards:
    - meeting.status must be "done"
    - meeting.vexa_meeting_id must be set
    - meeting.recording_deleted must be False

    On failure the meeting stays status="done" with recording_deleted=False.
    The background cleanup loop will retry later.

    SPEC: SPEC-GDPR-002-R1, R4
    """
    if meeting.status != "done":
        return
    if meeting.vexa_meeting_id is None:
        return
    if meeting.recording_deleted:
        return

    success = await delete_recording(meeting.vexa_meeting_id, str(meeting.id))
    if success:
        meeting.recording_deleted = True
        meeting.recording_deleted_at = datetime.now(UTC)
        await db.commit()


async def recording_cleanup_loop() -> None:
    """Background task: periodically clean up recordings for done meetings.

    Runs every CLEANUP_INTERVAL seconds.  Only targets meetings older than
    CLEANUP_AGE_MINUTES that still have recording_deleted=False.

    SPEC: SPEC-GDPR-002-R5
    """
    await asyncio.sleep(60)  # let the app finish starting
    while True:
        try:
            async with AsyncSessionLocal() as db:
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
                            "Recording cleanup loop: error for meeting %s: %s",
                            meeting.id,
                            exc,
                        )

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Recording cleanup loop: unexpected error")

        await asyncio.sleep(CLEANUP_INTERVAL)
