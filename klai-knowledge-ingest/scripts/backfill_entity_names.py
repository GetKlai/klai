"""Backfill entity_names on Qdrant chunks for already-ingested artifacts.

For every artifact that has entity_uuids stored on its chunks (set by Graphiti
on first ingest), this script:
  1. Resolves the UUIDs to entity names via FalkorDB (Cypher MATCH).
  2. Calls qdrant_store.set_entity_graph_data with the names — which scrolls
     all chunks of the artifact and writes per-chunk entity_names filtered by
     literal substring presence in chunk text.

Usage (in a knowledge-ingest container):

    docker exec klai-core-knowledge-ingest-1 \
        python -m scripts.backfill_entity_names --org-id <org_id> [--kb-slug <slug>] [--dry-run]

Without --kb-slug, all KBs of the org are backfilled.

Resumability: the script is idempotent. set_payload overwrites whatever was
there, so re-running on a partially-completed backfill is safe. If a chunk
already has entity_names, they get re-derived from the same source-of-truth
(FalkorDB) and overwritten.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict

import structlog
from qdrant_client.models import FieldCondition, Filter, MatchValue

from knowledge_ingest import qdrant_store
from knowledge_ingest.config import settings
from knowledge_ingest.qdrant_store import COLLECTION

logger = structlog.get_logger()

_SCROLL_BATCH = 200


async def _collect_artifacts_with_uuids(
    org_id: str,
    kb_slug: str | None,
) -> dict[str, set[str]]:
    """Scroll Qdrant once and return {artifact_id: {entity_uuid, ...}}.

    Each artifact's full UUID set is the union over its chunks (every chunk of
    an artifact gets the same entity_uuids list at write time, but defensive).
    """
    client = qdrant_store.get_client()

    must = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if kb_slug:
        must.append(FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)))
    scroll_filter = Filter(must=must)

    artifact_uuids: dict[str, set[str]] = defaultdict(set)
    offset = None
    seen_chunks = 0
    while True:
        points, next_offset = await client.scroll(
            collection_name=COLLECTION,
            scroll_filter=scroll_filter,
            limit=_SCROLL_BATCH,
            offset=offset,
            with_payload=["artifact_id", "entity_uuids"],
            with_vectors=False,
        )
        if not points:
            break
        for point in points:
            payload = point.payload or {}
            artifact_id = payload.get("artifact_id")
            uuids = payload.get("entity_uuids")
            if not artifact_id or not uuids:
                continue
            if not isinstance(uuids, list):
                continue
            artifact_uuids[str(artifact_id)].update(str(u) for u in uuids if u)
            seen_chunks += 1
        if next_offset is None:
            break
        offset = next_offset

    logger.info(
        "backfill_collect_artifacts_done",
        org_id=org_id,
        kb_slug=kb_slug,
        artifacts_with_entities=len(artifact_uuids),
        chunks_scanned=seen_chunks,
    )
    return artifact_uuids


async def _resolve_uuids_to_names(
    org_id: str,
    uuids: set[str],
) -> dict[str, str]:
    """Resolve entity UUIDs to names via FalkorDB. Returns {uuid: name}."""
    if not uuids or not settings.graphiti_enabled:
        return {}

    try:
        from falkordb import FalkorDB as FalkorDBClient
    except ImportError:
        logger.warning("falkordb_client_unavailable", org_id=org_id)
        return {}

    client = FalkorDBClient(host=settings.falkordb_host, port=settings.falkordb_port)
    graph = client.select_graph(org_id)

    uuid_list = list(uuids)
    result = graph.query(
        "MATCH (n:Entity) WHERE n.uuid IN $uuids RETURN n.uuid AS uuid, n.name AS name",
        params={"uuids": uuid_list},
    )

    name_map: dict[str, str] = {}
    for row in result.result_set or []:
        if not row or len(row) < 2:
            continue
        uid = str(row[0]) if row[0] is not None else None
        name = str(row[1]).strip() if row[1] is not None else None
        if uid and name:
            name_map[uid] = name
    return name_map


async def _backfill_artifact(
    artifact_id: str,
    org_id: str,
    entity_names: list[str],
    dry_run: bool,
) -> int:
    """Run set_entity_graph_data for one artifact. Returns chunks written."""
    if not entity_names:
        return 0

    if dry_run:
        logger.info(
            "backfill_dry_run",
            artifact_id=artifact_id,
            org_id=org_id,
            entity_names_sample=entity_names[:5],
            entity_name_count=len(entity_names),
        )
        return 0

    # Pass empty entity_uuids so the document-level write is skipped — uuids
    # are already on the chunks. We only want to add the per-chunk name field.
    await qdrant_store.set_entity_graph_data(
        artifact_id=artifact_id,
        org_id=org_id,
        entity_uuids=[],
        pagerank_scores={},
        entity_names=entity_names,
    )
    return len(entity_names)


async def run_backfill(
    org_id: str,
    kb_slug: str | None,
    dry_run: bool,
) -> None:
    artifact_uuids = await _collect_artifacts_with_uuids(org_id, kb_slug)
    if not artifact_uuids:
        logger.info("backfill_nothing_to_do", org_id=org_id, kb_slug=kb_slug)
        return

    all_uuids: set[str] = set()
    for uuids in artifact_uuids.values():
        all_uuids.update(uuids)
    name_map = await _resolve_uuids_to_names(org_id, all_uuids)

    logger.info(
        "backfill_resolved_names",
        org_id=org_id,
        unique_uuids=len(all_uuids),
        resolved=len(name_map),
        unresolved=len(all_uuids) - len(name_map),
    )

    successes = 0
    failures = 0
    for artifact_id, uuids in artifact_uuids.items():
        names = [name_map[u] for u in uuids if u in name_map]
        if not names:
            continue
        try:
            await _backfill_artifact(artifact_id, org_id, names, dry_run)
            successes += 1
        except Exception:
            logger.exception("backfill_artifact_failed", artifact_id=artifact_id)
            failures += 1

    logger.info(
        "backfill_done",
        org_id=org_id,
        kb_slug=kb_slug,
        artifacts_processed=successes,
        artifacts_failed=failures,
        dry_run=dry_run,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill entity_names on Qdrant chunks from FalkorDB graph."
    )
    parser.add_argument("--org-id", required=True, help="Org ID to backfill.")
    parser.add_argument("--kb-slug", default=None, help="Optional: limit to a single KB.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List artifacts that would be touched without writing to Qdrant.",
    )
    args = parser.parse_args()

    asyncio.run(run_backfill(args.org_id, args.kb_slug, args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
