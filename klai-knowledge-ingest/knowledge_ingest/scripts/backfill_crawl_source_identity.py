"""Backfill crawl source identity in Qdrant.

SPEC-RAG-EVIDENCE-INTEGRITY-001 phase 5 operator step. Do not run until the
SPEC startgate is met: source-domain ingest verified in production, ranking
contract active for 7 days, and citation-rescue shadow review completed.

Two independent passes:

1. Source-identity pass (always): crawl chunks with ``source_domain`` empty
   get ``source_domain`` + ``source_label`` derived from ``source_url``.
   Never touches ``taxonomy_node_ids``.
2. Stale-taxonomy pass (only with ``--clean-stale-taxonomy``): every chunk
   with a non-empty ``taxonomy_node_ids`` payload gets node-IDs that no
   longer exist in ``portal_taxonomy_nodes`` removed. Runs over ALL chunks
   with taxonomy IDs — not only the source-identity candidates — so stale
   IDs on already-backfilled or non-crawl chunks are cleaned too
   (acceptance Scenario 10 group b).

# @MX:WARN: [AUTO] Operator-only script that batch-mutates production Qdrant
# payloads. Run with --apply only after a --dry-run review; never wire into
# CI or an app startup path.
# @MX:REASON: REQ-SRC-03 mandates backfill as the FINAL phase, gated on the
# plan.md Fase-5 startgate. A premature or automated run makes the
# before/after analysis of the ranking contract uninterpretable.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from typing import Any
from urllib.parse import urlparse

import asyncpg
from qdrant_client.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

from knowledge_ingest import qdrant_store
from knowledge_ingest.config import settings
from knowledge_ingest.db import _parse_dsn

# Pause between mutation pages so a large backfill never saturates the
# Qdrant instance that is serving live /retrieve traffic.
_PAGE_PAUSE_SECONDS = 0.05


def source_identity_payload(payload: dict[str, Any]) -> dict[str, str]:
    """Derive the source-identity update from a chunk payload.

    Uses ``urlparse(...).netloc`` — the exact derivation the bulk-crawl
    ingest path uses (``crawler.py::_ingest_crawl_result`` sets
    ``source_domain=urlparse(url).netloc`` and ``compute_source_label``
    returns that domain verbatim for crawl chunks), so backfilled chunks
    are byte-identical to freshly ingested ones.
    """
    source_url = payload.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        return {}
    parsed = urlparse(source_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {}
    return {"source_domain": parsed.netloc, "source_label": parsed.netloc}


def cleaned_taxonomy_node_ids(
    payload: dict[str, Any],
    valid_node_ids: set[int],
) -> list[int] | None:
    raw_ids = payload.get("taxonomy_node_ids")
    if not isinstance(raw_ids, list):
        return None
    current = [int(item) for item in raw_ids if isinstance(item, int)]
    cleaned = [node_id for node_id in current if node_id in valid_node_ids]
    return cleaned if cleaned != current else None


async def _valid_taxonomy_node_ids() -> set[int]:
    conn = await asyncpg.connect(**_parse_dsn(settings.postgres_dsn))
    try:
        rows = await conn.fetch("SELECT id FROM portal_taxonomy_nodes")
        return {int(row["id"]) for row in rows}
    finally:
        await conn.close()


async def _source_identity_pass(
    client: Any,
    *,
    apply: bool,
    batch_size: int,
) -> tuple[int, int, Counter[str]]:
    """Fill source_domain/source_label on crawl chunks that miss them."""
    query_filter = Filter(
        must=[
            FieldCondition(key="source_type", match=MatchValue(value="crawl")),
            IsEmptyCondition(is_empty=PayloadField(key="source_domain")),
        ]
    )
    next_page = None
    scanned = 0
    mutated = 0
    source_counts: Counter[str] = Counter()

    while True:
        points, next_page = await client.scroll(
            collection_name=qdrant_store.COLLECTION,
            scroll_filter=query_filter,
            limit=batch_size,
            offset=next_page,
            # Only the fields this pass reads — chunk text and enrichment
            # payloads stay server-side.
            with_payload=["source_url"],
            with_vectors=False,
        )
        if not points:
            break

        # Group identical updates so a page becomes one set_payload call
        # per domain instead of one call per point.
        ids_by_domain: dict[str, list[Any]] = {}
        for point in points:
            scanned += 1
            update = source_identity_payload(point.payload or {})
            if not update:
                continue
            mutated += 1
            source_counts[update["source_domain"]] += 1
            ids_by_domain.setdefault(update["source_domain"], []).append(point.id)

        if apply:
            for domain, point_ids in ids_by_domain.items():
                await client.set_payload(
                    collection_name=qdrant_store.COLLECTION,
                    payload={"source_domain": domain, "source_label": domain},
                    points=point_ids,
                )
            if ids_by_domain:
                await asyncio.sleep(_PAGE_PAUSE_SECONDS)

        if next_page is None:
            break

    return scanned, mutated, source_counts


async def _stale_taxonomy_pass(
    client: Any,
    *,
    apply: bool,
    batch_size: int,
    valid_node_ids: set[int],
) -> tuple[int, int, Counter[str]]:
    """Remove taxonomy node-IDs that no longer exist in portal_taxonomy_nodes.

    Separate scroll over every chunk that HAS taxonomy IDs — deliberately not
    limited to the source-identity candidates, so stale IDs on chunks that
    already carry a source_domain are cleaned too.
    """
    query_filter = Filter(
        must_not=[
            IsEmptyCondition(is_empty=PayloadField(key="taxonomy_node_ids")),
        ]
    )
    next_page = None
    scanned = 0
    mutated = 0
    stale_counts: Counter[str] = Counter()

    while True:
        points, next_page = await client.scroll(
            collection_name=qdrant_store.COLLECTION,
            scroll_filter=query_filter,
            limit=batch_size,
            offset=next_page,
            with_payload=["taxonomy_node_ids", "kb_slug"],
            with_vectors=False,
        )
        if not points:
            break

        wrote_this_page = False
        for point in points:
            scanned += 1
            payload = point.payload or {}
            cleaned_ids = cleaned_taxonomy_node_ids(payload, valid_node_ids)
            if cleaned_ids is None:
                continue
            mutated += 1
            stale_counts[str(payload.get("kb_slug") or "_unknown")] += 1
            if apply:
                # Cleaned lists differ per point, so this pass stays
                # per-point by necessity.
                await client.set_payload(
                    collection_name=qdrant_store.COLLECTION,
                    payload={"taxonomy_node_ids": cleaned_ids},
                    points=[point.id],
                )
                wrote_this_page = True

        if wrote_this_page:
            await asyncio.sleep(_PAGE_PAUSE_SECONDS)
        if next_page is None:
            break

    return scanned, mutated, stale_counts


async def run_backfill(
    *,
    apply: bool,
    clean_stale_taxonomy: bool,
    batch_size: int,
) -> dict[str, Any]:
    client = qdrant_store.get_client()

    scanned, mutated, source_counts = await _source_identity_pass(
        client,
        apply=apply,
        batch_size=batch_size,
    )

    stale_counts: Counter[str] = Counter()
    if clean_stale_taxonomy:
        valid_node_ids = await _valid_taxonomy_node_ids()
        taxonomy_scanned, taxonomy_mutated, stale_counts = await _stale_taxonomy_pass(
            client,
            apply=apply,
            batch_size=batch_size,
            valid_node_ids=valid_node_ids,
        )
        scanned += taxonomy_scanned
        mutated += taxonomy_mutated

    return {
        "dry_run": not apply,
        "scanned": scanned,
        "mutated": mutated,
        "source_domain_counts": dict(source_counts),
        "stale_taxonomy_cleaned_by_kb": dict(stale_counts),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write payload updates; omitted means dry-run",
    )
    parser.add_argument("--clean-stale-taxonomy", action="store_true")
    parser.add_argument("--batch-size", type=int, default=256)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    report = await run_backfill(
        apply=args.apply,
        clean_stale_taxonomy=args.clean_stale_taxonomy,
        batch_size=args.batch_size,
    )
    print(report)


if __name__ == "__main__":
    asyncio.run(_main())
