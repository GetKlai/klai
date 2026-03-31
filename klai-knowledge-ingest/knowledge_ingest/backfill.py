"""Graphiti backfill: ingest existing artifacts into the knowledge graph.

Usage:
    docker exec klai-core-knowledge-ingest-1 python -m knowledge_ingest.backfill

Reads artifacts from PostgreSQL, fetches chunks from Qdrant, and calls
ingest_episode() for each. Resume-safe: checks for graphiti_episode_id
in artifact.extra before processing.
"""

import asyncio
import json
import logging
import time

import asyncpg
from qdrant_client import AsyncQdrantClient

from knowledge_ingest.config import settings
from knowledge_ingest.db import get_pool
from knowledge_ingest.graph import ingest_episode

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EPISODE_TIMEOUT = 600  # seconds per episode (large articles need more time)
MAX_TEXT_CHARS = 4000  # limit text per episode to reduce LLM calls

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("backfill")


async def main() -> None:
    pool = await get_pool()
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    # ---- Discover org --------------------------------------------------
    org = await pool.fetchrow("SELECT DISTINCT org_id FROM knowledge.artifacts LIMIT 1")
    if not org:
        log.error("No artifacts found — nothing to backfill")
        return
    org_id = str(org["org_id"])
    log.info("Org: %s", org_id)

    # ---- Get artifacts -------------------------------------------------
    rows = await pool.fetch(
        """
        SELECT id, path, content_type, created_at, extra
        FROM knowledge.artifacts
        WHERE org_id = $1
        ORDER BY created_at
        """,
        org_id,
    )
    total = len(rows)
    already = sum(
        1 for r in rows
        if r["extra"] and json.loads(r["extra"]).get("graphiti_episode_id")
    )
    to_process = [
        r for r in rows
        if not (r["extra"] and json.loads(r["extra"]).get("graphiti_episode_id"))
    ]
    log.info(
        "Found %d artifacts, %d already processed, %d to backfill",
        total, already, len(to_process),
    )
    if not to_process:
        log.info("Nothing to do")
        return

    # ---- Fetch all chunks from Qdrant once -----------------------------
    log.info("Fetching chunks from Qdrant collection '%s'...", settings.qdrant_collection)
    all_points, next_offset = await qdrant.scroll(
        collection_name=settings.qdrant_collection,
        limit=10000,
        with_payload=True,
        with_vectors=False,
    )
    chunks_by_artifact: dict[str, list[str]] = {}
    for pt in all_points:
        aid = (pt.payload or {}).get("artifact_id", "")
        text = (pt.payload or {}).get("text", "")
        if aid and text:
            chunks_by_artifact.setdefault(aid, []).append(text)
    log.info(
        "Loaded %d chunks for %d artifacts from Qdrant",
        len(all_points), len(chunks_by_artifact),
    )

    # ---- Process (sequential) ------------------------------------------
    # ingest_episode() owns concurrency control via its own semaphore.
    # Sequential processing here avoids creating 57 queued coroutines at once
    # and makes progress logging easier to follow.
    ok_count = 0
    err_count = 0
    t_start = time.time()
    total_to_process = len(to_process)

    for idx, row in enumerate(to_process, 1):
        artifact_id = str(row["id"])
        title = row["path"] or artifact_id
        content_type = row["content_type"] or "text"
        created_epoch = row["created_at"] or int(time.time())

        chunks = chunks_by_artifact.get(artifact_id, [])
        if not chunks:
            log.warning("[%d/%d] %s — no chunks, skipping", idx, total_to_process, title)
            continue

        full_text = "\n\n".join(chunks)
        if len(full_text) > MAX_TEXT_CHARS:
            full_text = full_text[:MAX_TEXT_CHARS]

        try:
            episode_id = await asyncio.wait_for(
                ingest_episode(
                    artifact_id=artifact_id,
                    document_text=full_text,
                    org_id=org_id,
                    content_type=content_type,
                    belief_time_start=created_epoch,
                ),
                timeout=EPISODE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            err_count += 1
            log.error(
                "[%d/%d] %s — TIMEOUT after %ds",
                idx, total_to_process, title, EPISODE_TIMEOUT,
            )
            continue
        except Exception as exc:
            err_count += 1
            log.error("[%d/%d] %s — %s", idx, total_to_process, title, exc)
            continue

        if episode_id is None:
            err_count += 1
            log.error("[%d/%d] %s — returned None (LLM issue?)", idx, total_to_process, title)
            continue

        # Success — persist for resume
        ok_count += 1
        await pool.execute(
            "UPDATE knowledge.artifacts "
            "SET extra = COALESCE(extra, '{}'::jsonb) || $1::jsonb "
            "WHERE id = $2::uuid",
            json.dumps({
                "graphiti_episode_id": episode_id,
                "graphiti_model": settings.graphiti_llm_model,
            }),
            artifact_id,
        )

        elapsed = time.time() - t_start
        rate = ok_count / (elapsed / 3600) if elapsed > 0 else 0
        log.info(
            "[%d/%d] %s — OK episode=%s (%d/hr, %ds elapsed)",
            idx, total_to_process, title, episode_id, int(rate), int(elapsed),
        )

    log.info(
        "Backfill complete: %d OK, %d errors out of %d",
        ok_count, err_count, total_to_process,
    )


if __name__ == "__main__":
    asyncio.run(main())
