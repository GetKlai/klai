"""
PostgreSQL artifact tracking for knowledge-ingest.
"""
import time
import uuid

from knowledge_ingest.db import get_pool

_SENTINEL = 253402300800  # 9999-12-31 — sentinel value for "still active"


async def create_artifact(
    org_id: str,
    kb_slug: str,
    path: str,
    provenance_type: str,
    assertion_mode: str,
    synthesis_depth: int,
    confidence: str | None,
    belief_time_start: int,
    belief_time_end: int,
    user_id: str | None = None,
) -> str:
    """Create a knowledge artifact record. Returns the artifact UUID."""
    artifact_id = str(uuid.uuid4())
    now = int(time.time())
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO knowledge.artifacts
          (id, org_id, user_id, kb_slug, path,
           provenance_type, assertion_mode,
           synthesis_depth, confidence,
           belief_time_start, belief_time_end, created_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        """,
        artifact_id, org_id, user_id, kb_slug, path,
        provenance_type, assertion_mode,
        synthesis_depth, confidence,
        belief_time_start, belief_time_end, now,
    )
    return artifact_id


async def soft_delete_artifact(org_id: str, kb_slug: str, path: str) -> None:
    """Set belief_time_end = now for all active artifacts matching this path."""
    now = int(time.time())
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE knowledge.artifacts
        SET belief_time_end = $1
        WHERE org_id = $2 AND kb_slug = $3 AND path = $4
          AND belief_time_end = $5
        """,
        now, org_id, kb_slug, path, _SENTINEL,
    )
