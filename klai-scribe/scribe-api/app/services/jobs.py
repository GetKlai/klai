"""In-process transcription queue backed by the `scribe.transcriptions` row.

The upload and retry routes insert or reset a `processing` row, call
`enqueue(txn_id)` and wait a bounded time for the result. Workers claim rows
with `FOR UPDATE SKIP LOCKED`, so the row itself is the queue: nothing is lost
on restart, and a claim older than `STALE_MINUTES` is picked up again.

During a deploy the old and the new container can briefly run the same job,
and a stale claim is taken over by another worker. Every final transition
therefore re-reads the row under a row lock and only writes while it is still
`processing` AND still carries the `claimed_at` this job was given: a later
claim (takeover, retry, startup release) changes that value, so a finisher
that lost its claim never overwrites the newer attempt, never fails it, and
never resurrects a deleted row.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import HTTPException
from sqlalchemy import or_, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.transcription import Transcription
from app.services.audio_storage import finalize_success, read_audio
from app.services.providers import get_provider

logger = structlog.get_logger(__name__)

# A job is claimed at most this many times; a row that crashed the process
# this often is marked failed instead of being retried forever.
MAX_ATTEMPTS = 3
# Longer than the longest realistic job (a 3-hour recording takes about
# 5 minutes; with backend backpressure retries on every chunk it can reach
# 15), so only a claim whose process died is taken over.
STALE_MINUTES = 30
# Fallback poll for rows nobody enqueued in this process (restart, deploy).
_POLL_SECONDS = 3.0

_CLAIM_SQL = text(
    "UPDATE scribe.transcriptions SET claimed_at = now(), attempts = attempts + 1 "
    "WHERE id = ("
    "SELECT id FROM scribe.transcriptions "
    "WHERE status = 'processing' AND audio_path IS NOT NULL "
    "AND attempts < :max_attempts "
    "AND (claimed_at IS NULL OR claimed_at < now() - make_interval(mins => :stale_minutes)) "
    "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
    ") RETURNING id"
)

_workers: set[asyncio.Task[None]] = set()
_waiters: dict[str, list[asyncio.Future[None]]] = {}
_wake: asyncio.Event | None = None


def _title_from_text(text: str | None, max_words: int = 8) -> str | None:
    """Generate a short title from the first words of a transcription."""
    if not text:
        return None
    words = text.split()[:max_words]
    title = " ".join(words)
    if len(text.split()) > max_words:
        title += "…"
    return title


def enqueue(txn_id: str) -> Awaitable[None]:
    """Wake the workers and return an awaitable that resolves when `txn_id` finishes.

    The caller bounds the wait with `asyncio.wait_for`; a timeout only drops
    this waiter, the job itself keeps running. The waiter is registered when
    the awaitable starts, so a zero budget (which cancels it before it runs)
    registers nothing and leaks nothing.
    """
    if _wake is not None:
        _wake.set()
    return _wait(txn_id)


async def _wait(txn_id: str) -> None:
    fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    _waiters.setdefault(txn_id, []).append(fut)
    try:
        await fut
    finally:
        waiters = _waiters.get(txn_id)
        if waiters and fut in waiters:
            waiters.remove(fut)
            if not waiters:
                del _waiters[txn_id]


def _notify(txn_id: str) -> None:
    for fut in _waiters.pop(txn_id, []):
        if not fut.done():
            fut.set_result(None)


async def _claim(session: AsyncSession) -> str | None:
    result = await session.execute(
        _CLAIM_SQL,
        {"max_attempts": MAX_ATTEMPTS, "stale_minutes": STALE_MINUTES},
    )
    txn_id = result.scalar_one_or_none()
    await session.commit()
    return txn_id


async def _finish(
    session: AsyncSession,
    txn_id: str,
    claimed_at: datetime | None,
    apply: Callable[[Transcription], None],
) -> str:
    """Apply a final transition only while this job still owns the row.

    Ownership means the row is still `processing` and `claimed_at` is the
    value this job read right after its claim. Returns the row's status
    afterwards, "deleted" when the row is gone, or "overtaken" when another
    claim replaced this one.
    """
    record = await session.get(Transcription, txn_id, with_for_update=True, populate_existing=True)
    if record is None or record.status != "processing" or record.claimed_at != claimed_at:
        # Read before the rollback, which expires the instance.
        if record is None:
            current = "deleted"
        elif record.status != "processing":
            current = record.status
        else:
            current = "overtaken"
        await session.rollback()
        return current
    apply(record)
    await session.commit()
    return record.status


def _fail(reason: str) -> Callable[[Transcription], None]:
    def apply(record: Transcription) -> None:
        record.status = "failed"
        record.error_reason = reason

    return apply


def _succeed(result: Any) -> Callable[[Transcription], None]:
    def apply(record: Transcription) -> None:
        # Deletes the audio first, then mutates the record (REQ-36.1).
        finalize_success(record, result)
        if record.name and record.name.lower().startswith("recording."):
            record.name = _title_from_text(result.text)

    return apply


async def run_job(session: AsyncSession, txn_id: str) -> None:
    started = time.monotonic()
    record = await session.get(Transcription, txn_id, populate_existing=True)
    if record is None or record.status != "processing":
        return
    attempts = record.attempts
    claimed_at = record.claimed_at
    audio_path = record.audio_path
    language_hint = record.language_hint
    # End the read transaction so no connection idles through the provider call.
    await session.commit()
    logger.info("transcription_job_started", txn_id=txn_id, attempts=attempts)

    try:
        try:
            wav_bytes = await asyncio.to_thread(read_audio, audio_path)
        except HTTPException:
            # read_audio answers 410: the audio was purged by another finisher
            # or deleted together with the transcription.
            final_status = await _finish(session, txn_id, claimed_at, _fail("audio_missing"))
        else:
            result = await get_provider().transcribe(wav_bytes, language_hint)
            final_status = await _finish(session, txn_id, claimed_at, _succeed(result))
    except Exception:
        logger.warning("transcription_job_failed", txn_id=txn_id, attempts=attempts, exc_info=True)
        await session.rollback()
        final_status = await _finish(session, txn_id, claimed_at, _fail("transcription_error"))

    logger.info(
        "transcription_job_finished",
        txn_id=txn_id,
        attempts=attempts,
        duration_s=round(time.monotonic() - started, 1),
        status=final_status,
    )


async def _worker(
    wake: asyncio.Event,
    claim: Callable[[AsyncSession], Awaitable[str | None]] = _claim,
) -> None:
    while True:
        # Clear before claiming: a wake-up that lands during the claim is
        # either seen by the claim (row committed) or kept for the next wait.
        wake.clear()
        txn_id: str | None = None
        try:
            async with AsyncSessionLocal() as session:
                txn_id = await claim(session)
                if txn_id is not None:
                    await run_job(session, txn_id)
        except Exception:
            # DB unreachable or a commit failed: the row keeps its claim and is
            # taken over after STALE_MINUTES; the worker itself must survive.
            logger.warning("transcription_worker_error", txn_id=txn_id, exc_info=True)
        finally:
            if txn_id is not None:
                _notify(txn_id)
        if txn_id is None:
            try:
                await asyncio.wait_for(wake.wait(), timeout=_POLL_SECONDS)
            except TimeoutError:
                pass


def stale_claim_filter(minutes: int = STALE_MINUTES):
    """Rows nobody is running: never claimed, or claimed longer than `minutes` ago."""
    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    return or_(Transcription.claimed_at.is_(None), Transcription.claimed_at < cutoff)


async def _recover_on_startup() -> None:
    """Fail exhausted rows nobody is running, then release the previous
    process's claims so its interrupted jobs are picked up right away.

    A compose deploy stops the old container before starting this one, so a
    claim seen here normally belongs to a dead process. Should the two ever
    overlap, the release makes the old job's finisher lose ownership (its
    `claimed_at` no longer matches) and the new claim redoes the work: wasted
    minutes, never a wrong or lost result. Audio stays on disk, as with the
    reaper.
    """
    async with AsyncSessionLocal() as session:
        exhausted = await session.execute(
            update(Transcription)
            .where(Transcription.status == "processing")
            .where(Transcription.attempts >= MAX_ATTEMPTS)
            .where(stale_claim_filter())
            .values(status="failed", error_reason="attempts_exhausted")
        )
        await session.execute(
            update(Transcription)
            .where(Transcription.status == "processing")
            .values(claimed_at=None)
        )
        await session.commit()
    if exhausted.rowcount:
        logger.warning("transcription_attempts_exhausted", count=exhausted.rowcount)


async def start() -> None:
    global _wake
    _wake = asyncio.Event()
    try:
        await _recover_on_startup()
    except Exception:
        # Same rule as the startup reaper: never block app startup. Stale
        # claims are then taken over after STALE_MINUTES instead.
        logger.warning("transcription_startup_recovery_failed", exc_info=True)
    for _ in range(settings.transcription_workers):
        _workers.add(asyncio.create_task(_worker(_wake)))


async def stop() -> None:
    """Cancel the workers. A running job keeps its row `processing` + claimed."""
    for task in _workers:
        task.cancel()
    await asyncio.gather(*_workers, return_exceptions=True)
    _workers.clear()
