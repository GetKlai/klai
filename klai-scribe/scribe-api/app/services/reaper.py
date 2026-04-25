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

from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transcription import Transcription

logger = structlog.get_logger(__name__)


async def reap_stranded(session: AsyncSession, timeout_min: int) -> int:
    """Flip processing rows older than `timeout_min` to `failed`.

    Returns the number of rows reaped. Caller owns the session lifecycle —
    this function commits its own changes once and returns.

    SPEC-SEC-HYGIENE-001 REQ-35.1, REQ-35.2, REQ-35.3.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=timeout_min)

    result = await session.execute(
        select(Transcription)
        .where(Transcription.status == "processing")
        .where(Transcription.created_at < cutoff)
    )
    stranded = list(result.scalars().all())

    if not stranded:
        return 0

    now = datetime.utcnow()
    for record in stranded:
        # `created_at` is the start time (set when the row is inserted at the
        # very beginning of the transcribe handler). Use it as the "started_at"
        # surrogate per AC-35 step 1 — see SPEC HY-35 audit notes.
        age_minutes = (now - record.created_at).total_seconds() / 60
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
