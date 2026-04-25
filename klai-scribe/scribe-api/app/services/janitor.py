"""Orphan-audio sweeper.

The retention policy in `audio_storage.py` says only `failed` records
should have an audio file at rest. SPEC-SEC-HYGIENE-001 REQ-36.2 covers
the rare crash window where `delete_audio` succeeded but the surrounding
DB transaction never committed — the file is gone from the file system
yet still referenced in the row, OR (less common) a delete + commit
happened but a writer outside the retention path left a stray file.

The janitor scans the audio base for `.wav` files that are NOT referenced
by any `transcriptions.audio_path` and removes them after a grace period.
The grace period exists so that an in-flight save (file written, row not
yet inserted) does not get clobbered.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transcription import Transcription

logger = structlog.get_logger(__name__)


async def sweep_orphans(
    session: AsyncSession,
    base: Path,
    grace_hours: int,
) -> int:
    """Delete `.wav` files in `base` that are not referenced by the DB.

    Files younger than `grace_hours` are preserved (in-flight save guard).
    Returns the number of files deleted.

    SPEC-SEC-HYGIENE-001 REQ-36.2.
    """
    if not base.exists():
        return 0

    result = await session.execute(
        select(Transcription.audio_path).where(Transcription.audio_path.isnot(None))
    )
    referenced = {row[0] for row in result.all() if row[0]}

    now = time.time()
    grace_seconds = grace_hours * 3600
    deleted = 0

    for file_path in base.rglob("*.wav"):
        if not file_path.is_file():
            continue

        # Normalise to forward-slash separator so cross-platform stored
        # paths (Linux prod, Windows dev) compare correctly against DB rows.
        rel = str(file_path.relative_to(base)).replace(os.sep, "/")
        if rel in referenced:
            continue

        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            # Race: file disappeared between rglob and stat — nothing to do.
            continue

        age_seconds = now - mtime
        if age_seconds < grace_seconds:
            continue

        try:
            file_path.unlink()
        except OSError:
            logger.warning("scribe_janitor_unlink_failed", path=str(file_path), exc_info=True)
            continue

        deleted += 1
        logger.info(
            "scribe_janitor_orphan_deleted",
            path=str(file_path),
            age_hours=round(age_seconds / 3600, 2),
        )

    return deleted
