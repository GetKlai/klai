"""
One-time migration script: klai_knowledge → klai_knowledge_v2

Creates the v2 collection with named vectors (vector_chunk + vector_questions),
re-indexes all existing artifacts through the enrichment pipeline, then reports
a switchover command.

Usage:
    python -m knowledge_ingest.scripts.migrate_collection [--dry-run] [--batch-size N]

After validation, switch the active collection:
    Set QDRANT_COLLECTION=klai_knowledge_v2 in /opt/klai/.env and restart knowledge-ingest.
"""
import argparse
import asyncio
import logging
import sys

import asyncpg

from knowledge_ingest import embedder, enrichment, qdrant_store
from knowledge_ingest.config import settings
from knowledge_ingest.db import _parse_dsn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def migrate(dry_run: bool, batch_size: int) -> None:
    logger.info("Starting collection migration (dry_run=%s, batch_size=%d)", dry_run, batch_size)

    # Connect to PostgreSQL to fetch all artifacts
    kwargs = _parse_dsn(settings.postgres_dsn)
    pool = await asyncpg.create_pool(**kwargs, min_size=2, max_size=5)

    try:
        # Ensure v2 collection exists (qdrant_store.ensure_collection creates it)
        await qdrant_store.ensure_collection()
        logger.info("v2 collection %s ready.", qdrant_store.COLLECTION_V2)

        # Fetch all non-deleted artifacts
        rows = await pool.fetch(
            "SELECT id::text, org_id, kb_slug, path, synthesis_depth FROM knowledge.artifacts "
            "WHERE deleted_at IS NULL ORDER BY created_at"
        )
        total = len(rows)
        logger.info("Found %d artifacts to migrate.", total)

        processed = 0
        errors = 0

        for row in rows:
            artifact_id = row["id"]
            org_id = row["org_id"]
            kb_slug = row["kb_slug"]
            path = row["path"]
            synthesis_depth = row["synthesis_depth"] or 0

            if dry_run:
                logger.info("[DRY-RUN] Would migrate %s/%s (org=%s)", kb_slug, path, org_id)
                processed += 1
                continue

            try:
                # Fetch document content from Qdrant v1 (existing raw points)
                client = qdrant_store.get_client()
                from qdrant_client.models import FieldCondition, Filter, MatchValue
                existing_points, _ = await client.scroll(
                    qdrant_store.COLLECTION,
                    scroll_filter=Filter(
                        must=[
                            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                            FieldCondition(key="artifact_id", match=MatchValue(value=artifact_id)),
                        ]
                    ),
                    limit=500,
                    with_payload=True,
                )

                if not existing_points:
                    logger.warning("No Qdrant points for artifact %s (%s/%s) — skipping", artifact_id, kb_slug, path)
                    continue

                # Reconstruct chunks and document text from existing points
                existing_points.sort(key=lambda p: p.payload.get("chunk_index", 0))
                chunks = [p.payload.get("text", "") for p in existing_points]
                document_text = "\n\n".join(chunks)  # Approximate; title from payload
                title = existing_points[0].payload.get("title", path)
                extra_payload = {
                    k: existing_points[0].payload[k]
                    for k in ("title", "source_type", "tags", "provenance_type", "confidence", "artifact_id")
                    if k in existing_points[0].payload
                }
                user_id = existing_points[0].payload.get("user_id")

                # Enrich chunks
                enriched_chunks = await enrichment.enrich_chunks(
                    document_text=document_text,
                    chunks=chunks,
                    title=title,
                    path=path,
                )

                # Embed enriched text
                enriched_texts = [ec.enriched_text for ec in enriched_chunks]
                chunk_vectors = await embedder.embed(enriched_texts)

                # Embed questions for depth 0-1
                question_vectors: list[list[float] | None]
                if synthesis_depth <= 1:
                    q_strings = [" ".join(ec.questions) if ec.questions else ec.original_text for ec in enriched_chunks]
                    question_vectors = await embedder.embed(q_strings)
                else:
                    question_vectors = [None] * len(enriched_chunks)

                await qdrant_store.upsert_enriched_chunks(
                    org_id=org_id,
                    kb_slug=kb_slug,
                    path=path,
                    enriched_chunks=enriched_chunks,
                    chunk_vectors=chunk_vectors,
                    question_vectors=question_vectors,
                    artifact_id=artifact_id,
                    extra_payload=extra_payload,
                    user_id=user_id,
                )

                processed += 1
                if processed % 100 == 0:
                    logger.info("Progress: %d/%d artifacts migrated", processed, total)

            except Exception as exc:
                logger.error("Failed to migrate artifact %s (%s/%s): %s", artifact_id, kb_slug, path, exc)
                errors += 1

        logger.info(
            "Migration complete: %d/%d processed, %d errors.",
            processed, total, errors,
        )

        if not dry_run:
            logger.info(
                "\nTo switch to the v2 collection, set:\n"
                "  QDRANT_COLLECTION=klai_knowledge_v2\n"
                "in /opt/klai/.env and restart knowledge-ingest."
            )

    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Qdrant collection to v2 with named vectors")
    parser.add_argument("--dry-run", action="store_true", help="List artifacts without migrating")
    parser.add_argument("--batch-size", type=int, default=10, help="Concurrent enrichment batch size")
    args = parser.parse_args()

    try:
        asyncio.run(migrate(dry_run=args.dry_run, batch_size=args.batch_size))
    except KeyboardInterrupt:
        logger.info("Migration interrupted.")
        sys.exit(1)


if __name__ == "__main__":
    main()
