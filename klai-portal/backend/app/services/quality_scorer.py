"""Qdrant quality score updater -- fire-and-forget payload updates.

# @MX:NOTE: [AUTO] Running average formula: (old * count + signal) / (count + 1). SPEC-KB-015.
# @MX:WARN: [AUTO] Fire-and-forget via asyncio.create_task. All errors silently discarded.
# @MX:REASON: REQ-KB-015-18 -- Qdrant updates are non-blocking, never propagate errors.

Uses httpx REST calls to Qdrant (NOT qdrant-client).
"""

import asyncio

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger()


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

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
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
        # prevent GC
        task.add_done_callback(lambda t: None)
    except RuntimeError:
        logger.warning("quality_score_schedule_failed: no running event loop")
