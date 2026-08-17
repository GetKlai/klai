"""Qdrant quality score updater -- fire-and-forget payload updates.

# @MX:NOTE: [AUTO] Running average formula: (old * count + signal) / (count + 1). SPEC-KB-015.
# @MX:WARN: [AUTO] Fire-and-forget via asyncio.create_task. All errors silently discarded.
# @MX:REASON: REQ-KB-015-18 -- Qdrant updates are non-blocking, never propagate errors.

Uses httpx REST calls to Qdrant (NOT qdrant-client).
"""

import asyncio
import weakref

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()

# Qdrant has no atomic increment for payload fields, so the read-compute-write
# below is a read-modify-write: two feedback events interleaving on the same
# chunk both read feedback_count = N and both write N + 1, and one vote vanishes
# with no error anywhere. That is worse than a rounding error, because the count
# IS the cold-start gate quality_boost opens at 3 (SPEC-KB-015 r.118) -- the lost
# vote can be the one that would have crossed it.
#
# One lock for all updates rather than one per chunk: two feedback events can
# share part of their chunk sets, and per-chunk locks would then need a global
# acquisition order to stay deadlock-free. Real feedback volume is single digits
# per month, so serialising costs nothing measurable and cannot deadlock.
#
# This closes the race completely as long as portal-api runs ONE process:
# scripts/uvicorn-launch.sh passes no --workers and entrypoint.sh adds only
# host/port, so uvicorn's default of 1 applies. Add workers or a second replica
# and this narrows the window instead of closing it -- at that point the lock
# has to move to Redis, which the feedback path already uses.
#
# Per running loop, NOT a single module-level Lock. A Lock binds to the loop
# that first awaits it and raises "bound to a different event loop" everywhere
# else -- and since this function swallows exceptions by contract
# (REQ-KB-015-18), that would turn into the feedback loop silently doing nothing
# for any caller on another loop (an operator script, a future worker, every
# test after the first). Weak keys so a finished loop does not pin itself in
# memory.
_UPDATE_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _update_lock() -> asyncio.Lock:
    """The update lock for the currently running loop, created on first use."""
    loop = asyncio.get_running_loop()
    lock = _UPDATE_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _UPDATE_LOCKS[loop] = lock
    return lock


async def apply_quality_score(
    chunk_ids: list[str],
    rating: str,
    org_id: int,
) -> None:
    """Update quality_score and feedback_count on Qdrant chunks.

    Formula: quality_score_new = (quality_score_old * count + signal) / (count + 1)
    signal = 1.0 for thumbsUp, 0.0 for thumbsDown

    All errors are silently discarded (REQ-KB-015-18).
    Missing chunk_ids are silently skipped (REQ-KB-015-17).
    """
    signal = 1.0 if rating == "thumbsUp" else 0.0
    collection = settings.qdrant_collection
    base_url = settings.qdrant_url

    headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}

    try:
        # The lock spans read AND write; holding it only around either half
        # would leave exactly the gap it exists to close.
        async with _update_lock(), httpx.AsyncClient(timeout=5.0, headers=headers) as client:
            # Batch fetch current payloads
            resp = await client.post(
                f"{base_url}/collections/{collection}/points",
                json={"ids": chunk_ids, "with_payload": True},
            )
            resp.raise_for_status()
            points = resp.json().get("result", [])

            if not points:
                return

            # Compute updates for each point
            for point in points:
                point_id = point["id"]
                payload = point.get("payload", {})
                old_score = payload.get("quality_score", 0.5)
                old_count = payload.get("feedback_count", 0)

                new_count = old_count + 1
                new_score = (old_score * old_count + signal) / new_count

                await client.post(
                    f"{base_url}/collections/{collection}/points/payload",
                    json={
                        "payload": {
                            "quality_score": new_score,
                            "feedback_count": new_count,
                        },
                        "points": [point_id],
                    },
                )

            logger.info(
                "quality_score_updated",
                org_id=org_id,
                chunk_count=len(points),
                rating=rating,
            )
    except Exception:
        logger.warning("quality_score_update_failed", org_id=org_id, exc_info=True)


def schedule_quality_update(
    chunk_ids: list[str],
    rating: str,
    org_id: int,
) -> None:
    """Fire-and-forget quality score update via asyncio.create_task."""
    try:
        task = asyncio.create_task(apply_quality_score(chunk_ids, rating, org_id))
        # prevent GC — asyncio.Task.add_done_callback always invokes the
        # callback with the completed task; this callback only needs to exist.
        task.add_done_callback(lambda t: None)  # noqa: ARG005
    except RuntimeError:
        logger.warning("quality_score_schedule_failed: no running event loop")
