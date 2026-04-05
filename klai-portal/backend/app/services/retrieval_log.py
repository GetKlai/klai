"""Retrieval log service -- ephemeral Redis store for feedback correlation.

# @MX:NOTE: [AUTO] Stores retrieval context in Redis sorted sets with 1h TTL. SPEC-KB-015.
# @MX:WARN: [AUTO] All operations silently discard errors (REQ-KB-015-03).
# @MX:REASON: Fire-and-forget pattern -- retrieval log is best-effort, not critical path.

Redis key pattern: "rl:{org_id}:{user_id}" (sorted set, score = epoch timestamp)
Member: JSON blob with chunk_ids, reranker_scores, query_resolved, embedding_model_version
TTL: 3600s (1 hour) via EXPIRE on the key
"""

import json
from datetime import datetime

import structlog

from app.services.redis_client import get_redis_pool

logger = structlog.get_logger()

_TTL_SECONDS = 3600  # 1 hour


async def write_retrieval_log(
    org_id: int,
    user_id: str,
    chunk_ids: list[str],
    reranker_scores: list[float],
    query_resolved: str,
    embedding_model_version: str,
    retrieved_at: datetime,
) -> None:
    """Write a retrieval log entry to Redis sorted set.

    Key: rl:{org_id}:{user_id}
    Score: epoch timestamp of retrieved_at
    Member: JSON blob with retrieval context

    Silently discards on any error (REQ-KB-015-03).
    """
    try:
        pool = await get_redis_pool()
        if pool is None:
            return

        key = f"rl:{org_id}:{user_id}"
        epoch = retrieved_at.timestamp()

        entry = json.dumps(
            {
                "chunk_ids": chunk_ids,
                "reranker_scores": reranker_scores,
                "query_resolved": query_resolved,
                "embedding_model_version": embedding_model_version,
                "retrieved_at": epoch,
            }
        )

        await pool.zadd(key, {entry: epoch})
        await pool.expire(key, _TTL_SECONDS)

        logger.debug(
            "retrieval_log_written",
            org_id=org_id,
            user_id=user_id,
            chunk_count=len(chunk_ids),
        )
    except Exception:
        logger.warning("retrieval_log_write_failed", org_id=org_id, exc_info=True)


async def find_correlated_log(
    org_id: int,
    user_id: str,
    message_created_at: datetime,
) -> dict | None:
    """Find the retrieval log entry closest-before message_created_at.

    Window: [message_created_at - 60s, message_created_at + 10s]
    Selection: closest entry with retrieved_at <= message_created_at

    Returns None if no match or on any error.
    """
    try:
        pool = await get_redis_pool()
        if pool is None:
            return None

        key = f"rl:{org_id}:{user_id}"
        msg_epoch = message_created_at.timestamp()
        window_start = msg_epoch - 60
        window_end = msg_epoch + 10

        entries = await pool.zrangebyscore(key, window_start, window_end)
        if not entries:
            return None

        # Select closest-before: entry with retrieved_at closest to and <= message_created_at
        best = None
        best_diff = float("inf")
        for raw in entries:
            entry = json.loads(raw)
            retrieved_epoch = entry.get("retrieved_at", 0)
            diff = msg_epoch - retrieved_epoch
            if diff >= 0 and diff < best_diff:
                best = entry
                best_diff = diff

        # If no entry before message time, pick the closest overall
        if best is None:
            for raw in entries:
                entry = json.loads(raw)
                retrieved_epoch = entry.get("retrieved_at", 0)
                diff = abs(msg_epoch - retrieved_epoch)
                if diff < best_diff:
                    best = entry
                    best_diff = diff

        return best
    except Exception:
        logger.warning("retrieval_log_find_failed", org_id=org_id, exc_info=True)
        return None
