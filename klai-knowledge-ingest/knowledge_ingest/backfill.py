"""Graphiti backfill: ingest existing artifacts into the knowledge graph.

Usage:
    docker exec klai-core-knowledge-ingest-1 \
        python -m knowledge_ingest.backfill --org-id <org_id>
    docker exec klai-core-knowledge-ingest-1 \
        python -m knowledge_ingest.backfill --org-id <org_id> --limit 1

``--org-id`` is required. The tenant used to be read back from the artifacts
table with a bare LIMIT 1 and no ORDER BY, which is an unordered pick from what
is now 19 tenants.

``--kb-slug`` is optional and repeatable, and restricts the run to those
knowledge bases. A tenant mixes languages per knowledge base — Voys keeps its
Dutch help centre in ``support`` and an English vendor corpus in ``ascend`` —
so a rebuild scoped to one language is expressed as a set of knowledge bases.

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
from knowledge_ingest.enrichment_policy import (
    GRAPHITI_EXTRACTION_VERSION,
    graph_episode_skip_reason,
)
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


async def main(
    org_id: str,
    limit: int | None = None,
    kb_slugs: list[str] | None = None,
    concurrency: int = 1,
) -> None:
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
        # kb_slugs is an INCLUDE list, never an exclude list. A tenant mixes
        # languages per knowledge base — Voys keeps its Dutch help centre in
        # `support` and an English vendor corpus in `ascend` — and a rebuild is
        # usually scoped to one of them. An exclude list would silently pull in
        # every knowledge base added after the command was written.
        if kb_slugs:
            # A typo here would otherwise select nothing, log "Nothing to do"
            # and exit 0 — so an operator running a rebuild is told it
            # succeeded when it processed no documents at all. That is the
            # failure this whole change exists to prevent, one level up.
            known = {
                r["kb_slug"]
                for r in await conn.fetch(
                    "SELECT DISTINCT kb_slug FROM knowledge.artifacts WHERE org_id = $1",
                    org_id,
                )
            }
            missing = sorted(set(kb_slugs) - known)
            if missing:
                log.error(
                    "Unknown knowledge base(s) for org %s: %s — known: %s",
                    org_id,
                    ", ".join(missing),
                    ", ".join(sorted(known)),
                )
                return
            log.info("Knowledge bases: %s", ", ".join(sorted(kb_slugs)))
            rows = await conn.fetch(
                """
                SELECT id, kb_slug, path, content_type, created_at, extra
                FROM knowledge.artifacts
                WHERE org_id = $1 AND kb_slug = ANY($2::text[])
                ORDER BY created_at
                """,
                org_id,
                kb_slugs,
            )
        else:
            log.info("Knowledge bases: all")
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

        # ---- Process (bounded concurrency) ---------------------------------
        # This loop used to be strictly sequential, with a comment claiming
        # ingest_episode's own semaphore handled concurrency. It does not: that
        # semaphore bounds episodes ALREADY in flight, and a sequential caller
        # never puts more than one there. Measured during the #1148 rebuild,
        # raising GRAPHITI_MAX_CONCURRENT changed nothing — 8 LLM calls a
        # minute against an alias allowing 90, so a 726-document rebuild was on
        # course to take a day with the rate budget sitting idle.
        #
        # --concurrency is the caller-side dial. Documents are independent:
        # each reads its own chunks and writes its own artifact row. Progress
        # lines interleave, which is why each still carries its own index.
        sem = asyncio.Semaphore(max(1, concurrency))
        # One asyncpg connection, shared by every worker. asyncpg forbids
        # concurrent operations on a connection and raises "another operation
        # is in progress" — measured at 715 such errors within two minutes the
        # first time this loop ran concurrently without the lock. Serialising
        # the writes costs nothing worth measuring: they are millisecond UPDATEs
        # against the seconds each document spends in LLM calls.
        db_lock = asyncio.Lock()

        async def _db_execute(*args: object) -> str:
            async with db_lock:
                return await conn.execute(*args)

        async def _db_append_episode(artifact_id: str, episode_id: str) -> None:
            # Every touch of the shared connection goes through the lock, not
            # just the ones written as conn.execute. This one reaches it via a
            # pg_store helper and was missed the first time, leaving the very
            # bug the lock exists to prevent.
            async with db_lock:
                await pg_store.append_graphiti_episode_id(conn, artifact_id, episode_id)

        counts = {"ok": 0, "err": 0}
        t_start = time.time()
        total_to_process = len(to_process)

        async def _process(idx: int, row) -> None:
            async with sem:
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
                    await _db_execute(
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
                    return

                full_text = "\n\n".join(chunks)

                # Same rule the live ingest route applies. This script is what a
                # graph rebuild runs, so without the check here every index page
                # comes straight back — along with the meta-facts and the ~26 LLM
                # calls each one costs. The route cannot cover this path: backfill
                # calls ingest_episode() directly.
                skip_reason = graph_episode_skip_reason(full_text)
                if skip_reason:
                    log.info("[%d/%d] %s — skipped (%s)", idx, total_to_process, title, skip_reason)
                    await _db_execute(
                        "UPDATE knowledge.artifacts "
                        "SET extra = COALESCE(extra, '{}'::jsonb) || $1::jsonb "
                        "WHERE id = $2::uuid",
                        json.dumps(
                            {
                                "graphiti_episode_id": f"skipped:{skip_reason}",
                                "graphiti_extraction_version": GRAPHITI_EXTRACTION_VERSION,
                            }
                        ),
                        artifact_id,
                    )
                    return

                # No truncation branch any more: split_episode_text spreads a long
                # document across several episodes instead of dropping its tail.
                episode_parts = split_episode_text(full_text)
                episode_ids: list[str] = []
                entity_graph_data = EntityGraphData()
                try:
                    await _db_execute(
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
                        await _db_append_episode(artifact_id, episode_id)

                    try:
                        await flush_entity_graph_data(artifact_id, org_id, entity_graph_data)
                    except Exception:
                        log.exception("%s — entity graph data update failed", title)
                    await _db_execute(
                        "UPDATE knowledge.artifacts "
                        "SET extra = COALESCE(extra, '{}'::jsonb) || $1::jsonb "
                        "WHERE id = $2::uuid",
                        json.dumps(
                            {
                                "graphiti_episode_complete": True,
                                "graphiti_model": settings.graphiti_llm_model,
                                "graphiti_extraction_version": GRAPHITI_EXTRACTION_VERSION,
                            }
                        ),
                        artifact_id,
                    )
                except TimeoutError:
                    counts["err"] += 1
                    log.error(
                        "[%d/%d] %s — TIMEOUT after %ds",
                        idx,
                        total_to_process,
                        title,
                        EPISODE_TIMEOUT,
                    )
                    return
                except Exception as exc:
                    counts["err"] += 1
                    log.error("[%d/%d] %s — %s", idx, total_to_process, title, exc)
                    return

                counts["ok"] += 1
                elapsed = time.time() - t_start
                rate = counts["ok"] / (elapsed / 3600) if elapsed > 0 else 0
                log.info(
                    "[%d/%d] %s — OK episodes=%s (%d/hr, %ds elapsed)",
                    idx,
                    total_to_process,
                    title,
                    episode_ids,
                    int(rate),
                    int(elapsed),
                )

        await asyncio.gather(
            *(_process(i, r) for i, r in enumerate(to_process, 1)), return_exceptions=True
        )
        ok_count = counts["ok"]
        err_count = counts["err"]
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
    parser.add_argument(
        "--kb-slug",
        action="append",
        dest="kb_slugs",
        default=None,
        help="Restrict to this knowledge base; repeat for several. "
        "Omit to process every knowledge base in the tenant.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Documents in flight at once. Default 1 keeps the historical "
        "sequential behaviour; raise it for a bulk rebuild. The real ceiling is "
        "the LiteLLM alias rpm/tpm, not this number.",
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            org_id=args.org_id,
            limit=args.limit,
            kb_slugs=args.kb_slugs,
            concurrency=args.concurrency,
        )
    )
