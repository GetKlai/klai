"""Graphiti backfill: ingest existing artifacts into the knowledge graph.

Usage:
    docker exec klai-core-knowledge-ingest-1 \
        python -m knowledge_ingest.backfill --org-id <org_id>
    docker exec klai-core-knowledge-ingest-1 \
        python -m knowledge_ingest.backfill --org-id <org_id> --limit 1

``--org-id`` is required. The tenant used to be read back from the artifacts
table with a bare LIMIT 1 and no ORDER BY, which is an unordered pick from what
is now 19 tenants.

Reads artifacts from PostgreSQL, fetches chunks from Qdrant, and calls
ingest_episode() for each text part. Resume-safe: prefers graphiti_episode_ids
and falls back to graphiti_episode_id before processing.

SPEC-TI-003-FOLLOWUP-001 AC-2: this is an operator one-shot that does
cross-org admin work (it picks the org via DISTINCT on first run, then
mass-updates artifacts.extra for every artifact in that org). It uses
``cross_org_admin_connection`` so the GUC marks the connection as a
deliberate admin caller. Once SPEC-TI-011 lands per-service roles, the
RLS policies will recognise the bypass; until then the klai superuser
connection bypasses RLS regardless.
"""

import argparse
import asyncio
import json
import logging
import time

from qdrant_client import AsyncQdrantClient

from knowledge_ingest import pg_store
from knowledge_ingest.config import settings
from knowledge_ingest.db import cross_org_admin_connection
from knowledge_ingest.enrichment_policy import graph_episode_skip_reason
from knowledge_ingest.episode_text import MAX_TEXT_CHARS, split_episode_text
from knowledge_ingest.graph import EntityGraphData, flush_entity_graph_data, ingest_episode

__all__ = ["MAX_TEXT_CHARS"]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
EPISODE_TIMEOUT = 600  # seconds per episode (large articles need more time)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("backfill")


def _has_graphiti_episode_record(raw_extra: str | dict | None) -> bool:
    if not raw_extra:
        return False
    extra = json.loads(raw_extra) if isinstance(raw_extra, str) else raw_extra
    if "graphiti_episode_ids" in extra:
        if not isinstance(extra["graphiti_episode_ids"], list):
            raise TypeError("graphiti_episode_ids must be a JSON list")
        expected_parts = extra.get("graphiti_episode_part_count")
        if expected_parts is not None:
            return (
                extra.get("graphiti_episode_complete") is True
                and len(extra["graphiti_episode_ids"]) >= expected_parts
            )
        return True
    return bool(extra.get("graphiti_episode_id"))


async def main(org_id: str, limit: int | None = None) -> None:
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    async with cross_org_admin_connection() as conn:
        # ---- Confirm the tenant exists -------------------------------------
        # The org is an argument, never discovered. It used to be read back
        # from the artifacts table with a bare LIMIT 1 and no ORDER BY — an
        # unordered pick from what is now 19 tenants, so an operator running a
        # rebuild for one customer could silently spend another customer's
        # rate budget writing episodes into their graph. The script opens a
        # cross_org_admin_connection precisely because it bypasses RLS, which
        # is what made the wrong-tenant outcome reachable rather than blocked.
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM knowledge.artifacts WHERE org_id = $1)",
            org_id,
        )
        if not exists:
            log.error("No artifacts for org %s — nothing to backfill", org_id)
            return
        log.info("Org: %s", org_id)

        # ---- Get artifacts -------------------------------------------------
        rows = await conn.fetch(
            """
            SELECT id, kb_slug, path, content_type, created_at, extra
            FROM knowledge.artifacts
            WHERE org_id = $1
            ORDER BY created_at
            """,
            org_id,
        )
        total = len(rows)
        already = sum(1 for r in rows if _has_graphiti_episode_record(r["extra"]))
        to_process = [r for r in rows if not _has_graphiti_episode_record(r["extra"])]
        log.info(
            "Found %d artifacts, %d already processed, %d to backfill",
            total,
            already,
            len(to_process),
        )
        if not to_process:
            log.info("Nothing to do")
            return

        if limit is not None:
            to_process = to_process[:limit]
            log.info("Limiting to %d artifact(s)", limit)

        # ---- Fetch all chunks from Qdrant (paginated) ----------------------
        log.info("Fetching chunks from Qdrant collection '%s'...", settings.qdrant_collection)
        chunks_by_artifact: dict[str, list[str]] = {}
        total_points = 0
        offset = None
        while True:
            batch, next_offset = await qdrant.scroll(
                collection_name=settings.qdrant_collection,
                offset=offset,
                limit=1000,
                with_payload=True,
                with_vectors=False,
            )
            for pt in batch:
                aid = (pt.payload or {}).get("artifact_id", "")
                text = (pt.payload or {}).get("text", "")
                if aid and text:
                    chunks_by_artifact.setdefault(aid, []).append(text)
            total_points += len(batch)
            if next_offset is None:
                break
            offset = next_offset
        log.info(
            "Loaded %d chunks for %d artifacts from Qdrant",
            total_points,
            len(chunks_by_artifact),
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
                log.warning(
                    "[%d/%d] %s — no chunks, marking as skipped",
                    idx,
                    total_to_process,
                    title,
                )
                await conn.execute(
                    "UPDATE knowledge.artifacts "
                    "SET extra = COALESCE(extra, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2::uuid",
                    json.dumps(
                        {
                            "graphiti_episode_id": "no-chunks",
                            "graphiti_episode_ids": [],
                        }
                    ),
                    artifact_id,
                )
                continue

            full_text = "\n\n".join(chunks)

            # Same rule the live ingest route applies. This script is what a
            # graph rebuild runs, so without the check here every index page
            # comes straight back — along with the meta-facts and the ~26 LLM
            # calls each one costs. The route cannot cover this path: backfill
            # calls ingest_episode() directly.
            skip_reason = graph_episode_skip_reason(full_text)
            if skip_reason:
                log.info("[%d/%d] %s — skipped (%s)", idx, total_to_process, title, skip_reason)
                await conn.execute(
                    "UPDATE knowledge.artifacts "
                    "SET extra = COALESCE(extra, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2::uuid",
                    json.dumps({"graphiti_episode_id": f"skipped:{skip_reason}"}),
                    artifact_id,
                )
                continue

            # No truncation branch any more: split_episode_text spreads a long
            # document across several episodes instead of dropping its tail.
            episode_parts = split_episode_text(full_text)
            episode_ids: list[str] = []
            entity_graph_data = EntityGraphData()
            try:
                await conn.execute(
                    "UPDATE knowledge.artifacts "
                    "SET extra = COALESCE(extra, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2::uuid",
                    json.dumps(
                        {
                            "graphiti_episode_part_count": len(episode_parts),
                            "graphiti_episode_complete": False,
                        }
                    ),
                    artifact_id,
                )
                for episode_text in episode_parts:
                    episode_id = await asyncio.wait_for(
                        ingest_episode(
                            artifact_id=artifact_id,
                            document_text=episode_text,
                            org_id=org_id,
                            content_type=content_type,
                            belief_time_start=created_epoch,
                            kb_slug=row["kb_slug"] or "",
                            path=row["path"] or "",
                            entity_graph_data=entity_graph_data,
                        ),
                        timeout=EPISODE_TIMEOUT,
                    )
                    if episode_id is None:
                        raise RuntimeError("returned None (LLM issue?)")
                    episode_ids.append(episode_id)
                    await pg_store.append_graphiti_episode_id(conn, artifact_id, episode_id)

                try:
                    await flush_entity_graph_data(artifact_id, org_id, entity_graph_data)
                except Exception:
                    log.exception("%s — entity graph data update failed", title)
                await conn.execute(
                    "UPDATE knowledge.artifacts "
                    "SET extra = COALESCE(extra, '{}'::jsonb) || $1::jsonb "
                    "WHERE id = $2::uuid",
                    json.dumps(
                        {
                            "graphiti_episode_complete": True,
                            "graphiti_model": settings.graphiti_llm_model,
                        }
                    ),
                    artifact_id,
                )
            except TimeoutError:
                err_count += 1
                log.error(
                    "[%d/%d] %s — TIMEOUT after %ds",
                    idx,
                    total_to_process,
                    title,
                    EPISODE_TIMEOUT,
                )
                continue
            except Exception as exc:
                err_count += 1
                log.error("[%d/%d] %s — %s", idx, total_to_process, title, exc)
                continue

            ok_count += 1
            elapsed = time.time() - t_start
            rate = ok_count / (elapsed / 3600) if elapsed > 0 else 0
            log.info(
                "[%d/%d] %s — OK episodes=%s (%d/hr, %ds elapsed)",
                idx,
                total_to_process,
                title,
                episode_ids,
                int(rate),
                int(elapsed),
            )

        log.info(
            "Backfill complete: %d OK, %d errors out of %d",
            ok_count,
            err_count,
            total_to_process,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Graphiti backfill")
    parser.add_argument(
        "--org-id",
        required=True,
        help="Zitadel org id to backfill. Required: this script bypasses RLS, "
        "so a wrong or missing tenant writes into someone else's graph.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process at most N artifacts")
    args = parser.parse_args()
    asyncio.run(main(org_id=args.org_id, limit=args.limit))
