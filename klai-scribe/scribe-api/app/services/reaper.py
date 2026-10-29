"""Stranded `processing` row reaper.

A worker OOM, container kill, or process crash mid-transcription leaves
a `transcriptions.status="processing"` row that no longer has a worker
attending to it. The UI sees "still processing" forever and a manual DB
fix is the only escape hatch.

This reaper runs at worker startup (see `app.main.lifespan`) and flips
stale `processing` rows that the job queue (`app.services.jobs`) can no
longer run, because they have no audio or used up their attempts, to
`failed` with `error_reason="worker_restart_stranded"`. A row with audio and
attempts left is waiting or running in the queue and is left alone.
The audio file is preserved (REQ-35.3) so a manual recovery / retry is
still possible.

SPEC-SEC-HYGIENE-001 REQ-35.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transcription import Transcription
from app.services.jobs import MAX_ATTEMPTS, stale_claim_filter

logger = structlog.get_logger(__name__)

# Hard upper bound per single reaper run to keep startup memory + tx size
# bounded after a long outage. If more than this remain, the next worker
# startup picks up the rest.
_REAP_BATCH_LIMIT = 500


async def reap_stranded(session: AsyncSession, timeout_min: int) -> int:
    """Flip unrunnable processing rows older than `timeout_min` to `failed`.

    Returns the number of rows reaped. Caller owns the session lifecycle —
    this function commits its own changes once and returns.

    Notes for the operator:
    - Only rows the queue cannot claim are reaped: `audio_path IS NULL`, or
      `attempts >= 3` (the queue's `MAX_ATTEMPTS`) with no live claim. A
      queued or running row with audio is never touched, however long the
      queue is.
    - `created_at` is the row insert time and serves as the age; see
      `Settings.scribe_stranded_timeout_min`.
    - When N replicas of scribe-api start simultaneously, all N race to
      reap the same rows. SQLAlchemy retries on row-lock conflict, so the
      net effect is correct but wastes a small amount of work. Scribe
      currently runs as a single replica; revisit if that changes.

    SPEC-SEC-HYGIENE-001 REQ-35.1, REQ-35.2, REQ-35.3.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=timeout_min)

    result = await session.execute(
        select(Transcription)
        .where(Transcription.status == "processing")
        .where(
            or_(
                Transcription.audio_path.is_(None),
                # Out of attempts, and not held by a job that is still running.
                and_(Transcription.attempts >= MAX_ATTEMPTS, stale_claim_filter()),
            )
        )
        .where(Transcription.created_at < cutoff)
        .limit(_REAP_BATCH_LIMIT)
    )
    stranded = list(result.scalars().all())

    if not stranded:
        return 0

    now = datetime.now(UTC)
    for record in stranded:
        # Compare against a TZ-aware now even if `record.created_at` was
        # written by a code path that produced a naive datetime — coerce
        # to UTC before the subtraction so the age math is correct in both
        # cases.
        created_at = record.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        age_minutes = (now - created_at).total_seconds() / 60
        record.status = "failed"
        record.error_reason = "worker_restart_stranded"
        # REQ-35.3: do NOT touch `audio_path` — the file stays on disk so a
        # manual recovery flow can replay it.
        logger.warning(
            "scribe_stranded_recovered",
            txn_id=record.id,
            age_minutes=round(age_minutes, 1),
        )

    await session.commit()
    return len(stranded)
