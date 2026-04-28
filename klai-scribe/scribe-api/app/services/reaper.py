"""Stranded `processing` row reaper.

A worker OOM, container kill, or process crash mid-transcription leaves
a `transcriptions.status="processing"` row that no longer has a worker
attending to it. The UI sees "still processing" forever and a manual DB
fix is the only escape hatch.

This reaper runs at worker startup (see `app.main.lifespan`) and flips
stale `processing` rows to `failed` with `error_reason="worker_restart_stranded"`.
The audio file is preserved (REQ-35.3) so a manual recovery / retry is
still possible.

SPEC-SEC-HYGIENE-001 REQ-35.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transcription import Transcription

logger = structlog.get_logger(__name__)

# Hard upper bound per single reaper run to keep startup memory + tx size
# bounded after a long outage. If more than this remain, the next worker
# startup picks up the rest.
_REAP_BATCH_LIMIT = 500


async def reap_stranded(session: AsyncSession, timeout_min: int) -> int:
    """Flip processing rows older than `timeout_min` to `failed`.

    Returns the number of rows reaped. Caller owns the session lifecycle —
    this function commits its own changes once and returns.

    Notes for the operator:
    - `created_at` is the row insert time (set at the very beginning of the
      transcribe handler) and doubles as the "started_at" surrogate. There
      is no separate `started_at` column. Pick `timeout_min` LARGER than
      the longest realistic transcription duration (typical scribe meeting
      is 30-60 min; default is 60 min — see `Settings.scribe_stranded_timeout_min`).
      A `timeout_min` that is too short will false-reap a still-running
      transcription. The mutation is recoverable (the original worker
      finishes and `finalize_success` overwrites status), but the user
      briefly sees `status="failed"` in the UI.
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
