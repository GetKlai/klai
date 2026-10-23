"""Operator command: count, and optionally write, the product label of chunks that have none.

Chunks ingested before the product label existed (knowledge-base plan, step 7)
carry neither ``product`` nor ``text_fingerprint``, so retrieval-api can neither
prefer them for a question's product nor recognise them as a lookalike. This
derives both from what the point already holds: the label from its source URL
or path and the knowledge base's product rules (product_label.py), the
fingerprint from its text.

Usage (inside the running container, or a throwaway clone of it):

    # Read-only: how many chunks have no label, and which labels they would get.
    python -m knowledge_ingest.scripts.product_backfill --org <org_id>

    # Write them, in batches. Safe to rerun and to stop: only points still
    # without a label are selected, and a written label is never changed.
    python -m knowledge_ingest.scripts.product_backfill --org <org_id> --execute
    python -m knowledge_ingest.scripts.product_backfill --org <org_id> --kb <kb_slug> --execute

An empty label is written too: it means the rules and defaults found no
product, and it keeps the point out of the next run's selection.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    FieldCondition,
    Filter,
    IsEmptyCondition,
    MatchValue,
    PayloadField,
    SetPayload,
    SetPayloadOperation,
)

from knowledge_ingest import kb_config
from knowledge_ingest.product_label import derive_product
from knowledge_ingest.qdrant_store import COLLECTION, get_client
from knowledge_ingest.utils.content_fingerprint import compute_minhash

_BATCH = 256
_PAYLOAD = ["kb_slug", "source_type", "source_url", "source_ref", "path", "text"]


async def backfill_products(
    client: AsyncQdrantClient,
    org_id: str,
    kb_slug: str | None = None,
    *,
    execute: bool,
) -> tuple[Counter, Counter]:
    """Label every in-scope point without a ``product``.

    Returns the counts (``missing`` selected, ``written``) and how many points
    get each label. Without ``execute`` nothing is written.
    """
    must = [FieldCondition(key="org_id", match=MatchValue(value=org_id))]
    if kb_slug:
        must.append(FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)))
    selection = Filter(must=[*must, IsEmptyCondition(is_empty=PayloadField(key="product"))])
    result: Counter = Counter()
    labels: Counter = Counter()
    offset = None
    while True:
        points, offset = await client.scroll(
            COLLECTION,
            scroll_filter=selection,
            limit=_BATCH,
            offset=offset,
            with_payload=_PAYLOAD,
            with_vectors=False,
        )
        operations = []
        for point in points:
            payload = point.payload or {}
            rules = await kb_config.get_kb_product_rules(org_id, payload.get("kb_slug", ""))
            update = {"product": derive_product(payload, rules)}
            if fingerprint := compute_minhash(payload.get("text") or ""):
                update["text_fingerprint"] = fingerprint
            labels[update["product"]] += 1
            operations.append(
                SetPayloadOperation(set_payload=SetPayload(payload=update, points=[point.id]))
            )
        result["missing"] += len(points)
        if execute and operations:
            await client.batch_update_points(COLLECTION, update_operations=operations)
            result["written"] += len(operations)
        if offset is None:
            return result, labels


async def _main(args: argparse.Namespace) -> None:
    result, labels = await backfill_products(get_client(), args.org, args.kb, execute=args.execute)
    for label, count in labels.most_common():
        print(f"{label or '(no product)':40} {count:>10}")
    if args.execute:
        print(f"backfill: {result['written']} of {result['missing']} points written")
    else:
        print(f"dry run: {result['missing']} points without a product label; nothing written")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", required=True, help="org_id (zitadel id) to count or label")
    parser.add_argument("--kb", help="limit to one knowledge base slug")
    parser.add_argument("--execute", action="store_true", help="write the missing labels")
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
