"""Operator command: count, and optionally repair, chunks without a sparse vector.

Chunks written before the base ingest embedded sparse vectors carry only the
dense vector until (and unless) enrichment rewrote them, and enrichment never
reached documents above 200 chunks. In retrieval-api the hybrid search then
falls back to dense-only for those chunks.

Usage (inside the running container, or a throwaway clone of it):

    # Read-only: per knowledge base, the chunks with and without a sparse
    # vector, with a context sentence, with questions.
    python -m knowledge_ingest.scripts.sparse_backfill --org <org_id>

    # Write the missing sparse vectors, in batches. Safe to rerun and to stop:
    # only points still without the vector are selected.
    python -m knowledge_ingest.scripts.sparse_backfill --org <org_id> --execute
    python -m knowledge_ingest.scripts.sparse_backfill --org <org_id> --kb <kb_slug> --execute

The embedded text is ``text_enriched`` when the chunk was enriched and ``text``
otherwise, the same input the enrichment path gives the sparse embedder. Only
the ``vector_sparse`` of a point is written; its other vectors and its payload
are left alone. A batch the sidecar cannot answer is counted as failed and
stays selected for the next run.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    HasVectorCondition,
    MatchValue,
    PointVectors,
)

from knowledge_ingest import sparse_embedder
from knowledge_ingest.config import settings
from knowledge_ingest.qdrant_store import COLLECTION, get_client

logger = structlog.get_logger()

_SCROLL_PAGE = 256
_COLUMNS = ("chunks", "sparse", "no_sparse", "context", "questions")


def _scope(org_id: str, kb_slug: str | None) -> list[FieldCondition]:
    must = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if kb_slug:
        must.append(FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)))
    return must


async def count_chunks(
    client: AsyncQdrantClient, org_id: str, kb_slug: str | None = None
) -> dict[str, Counter]:
    """Per knowledge base: how many chunks have a sparse vector, a context sentence, questions.

    Read-only. ``context`` and ``questions`` are payload fields only enrichment
    writes, so ``chunks - context`` is the number of chunks still on raw text.
    """
    per_kb: dict[str, Counter] = {}
    offset = None
    while True:
        points, offset = await client.scroll(
            COLLECTION,
            scroll_filter=Filter(must=_scope(org_id, kb_slug)),
            limit=_SCROLL_PAGE,
            offset=offset,
            with_payload=["kb_slug", "context_prefix", "questions"],
            with_vectors=["vector_sparse"],
        )
        for point in points:
            payload = point.payload or {}
            counts = per_kb.setdefault(payload.get("kb_slug", ""), Counter())
            has_sparse = bool((point.vector or {}).get("vector_sparse"))
            counts["chunks"] += 1
            counts["sparse" if has_sparse else "no_sparse"] += 1
            counts["context"] += bool(payload.get("context_prefix"))
            counts["questions"] += bool(payload.get("questions"))
        if offset is None:
            return per_kb


async def backfill_sparse(
    client: AsyncQdrantClient,
    org_id: str,
    kb_slug: str | None = None,
    *,
    execute: bool,
    batch_size: int | None = None,
) -> Counter:
    """Write ``vector_sparse`` on every in-scope point that lacks it.

    Returns ``missing`` (points selected), ``written`` and ``failed``. Without
    ``execute`` nothing is embedded or written and only ``missing`` is filled.
    """
    batch_size = batch_size or settings.sparse_sidecar_batch_size
    selection = Filter(
        must=_scope(org_id, kb_slug),
        must_not=[HasVectorCondition(has_vector="vector_sparse")],
    )
    result: Counter = Counter()
    offset = None
    while True:
        points, offset = await client.scroll(
            COLLECTION,
            scroll_filter=selection,
            limit=batch_size,
            offset=offset,
            with_payload=["text", "text_enriched"],
            with_vectors=False,
        )
        result["missing"] += len(points)
        if execute and points:
            texts = [
                (p.payload or {}).get("text_enriched") or (p.payload or {}).get("text") or ""
                for p in points
            ]
            sparse = await sparse_embedder.embed_sparse_batch(texts)
            updates = [
                PointVectors(id=p.id, vector={"vector_sparse": sv})
                for p, sv in zip(points, sparse, strict=True)
                if sv is not None
            ]
            if updates:
                try:
                    await client.update_vectors(COLLECTION, points=updates)
                except Exception:
                    # A point deleted by a concurrent re-ingest fails the whole
                    # call; the rest of the batch is picked up by the next run.
                    logger.warning("sparse_backfill_batch_failed", exc_info=True)
                    updates = []
            result["written"] += len(updates)
            result["failed"] += len(points) - len(updates)
        if offset is None:
            return result


def _print_counts(per_kb: dict[str, Counter]) -> None:
    print(f"{'kb_slug':40} " + " ".join(f"{c:>10}" for c in _COLUMNS))
    total: Counter = Counter()
    for kb, counts in sorted(per_kb.items()):
        total.update(counts)
        print(f"{kb:40} " + " ".join(f"{counts[c]:>10}" for c in _COLUMNS))
    print(f"{'TOTAL':40} " + " ".join(f"{total[c]:>10}" for c in _COLUMNS))


async def _main(args: argparse.Namespace) -> None:
    client = get_client()
    print("before:")
    _print_counts(await count_chunks(client, args.org, args.kb))
    result = await backfill_sparse(
        client, args.org, args.kb, execute=args.execute, batch_size=args.batch_size
    )
    if args.execute:
        print(f"backfill: {result['written']} written, {result['failed']} failed")
        print("after:")
        _print_counts(await count_chunks(client, args.org, args.kb))
    else:
        print(f"dry run: {result['missing']} points without a sparse vector; nothing written")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", required=True, help="org_id (zitadel id) to count or repair")
    parser.add_argument("--kb", help="limit to one knowledge base slug")
    parser.add_argument("--execute", action="store_true", help="write the missing vectors")
    parser.add_argument("--batch-size", type=int, help="points per sidecar call")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
