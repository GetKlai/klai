"""Replay stored documents through Graphiti to measure the extraction prompt.

Usage:
    python -m scripts.probe_extraction_prompt \
        --source-org-id <org_id> \
        --kb-slug <kb_slug> \
        --limit 5 \
        --scratch-org-id zz-prompt-validation-<name>

The source tenant is read-only: text comes from ``artifacts.extra.document_text``
and is never crawled again. The scratch graph is always deleted after the run.
"""

from __future__ import annotations

import argparse
import asyncio
import time
from collections import Counter
from collections.abc import Sequence
from typing import Any

from knowledge_ingest import graph as graph_module
from knowledge_ingest.config import settings
from knowledge_ingest.db import cross_org_admin_connection
from knowledge_ingest.enrichment_policy import graph_episode_skip_reason
from knowledge_ingest.episode_text import split_episode_text
from knowledge_ingest.graph import EntityGraphData, ingest_episode

SCRATCH_PREFIX = "zz-prompt-validation-"
META_MARKERS = (
    "handleiding",
    "documentatie",
    "documentatieartikel",
    "getiteld",
    "paginamap",
    "sectie",
)
LANGUAGE_MARKERS = (" the ", " is used ", " de ")


def _validate_scratch_prefix(scratch_org_id: str) -> None:
    if not scratch_org_id.startswith(SCRATCH_PREFIX):
        raise ValueError(f"--scratch-org-id must begin with {SCRATCH_PREFIX!r}")


def validate_probe_ids(source_org_id: str, scratch_org_id: str) -> None:
    _validate_scratch_prefix(scratch_org_id)
    if scratch_org_id == source_org_id:
        raise ValueError("--scratch-org-id and --source-org-id must differ")


def _open_graph(org_id: str):
    from falkordb import FalkorDB as FalkorDBClient

    client = FalkorDBClient(host=settings.falkordb_host, port=settings.falkordb_port)
    return client.select_graph(org_id)


def count_graph_nodes(org_id: str) -> int:
    graph = _open_graph(org_id)
    result = graph.query(
        "MATCH (n) WHERE n.group_id = $org_id RETURN count(n) AS remaining",
        params={"org_id": org_id},
    )
    if not result.result_set:
        return 0
    return int(result.result_set[0][0] or 0)


def delete_scratch_graph(scratch_org_id: str) -> int:
    # Re-check at the destructive boundary; callers cannot validate one value
    # and accidentally pass another value to the wipe.
    _validate_scratch_prefix(scratch_org_id)
    deleted = graph_module.wipe_org_graph(scratch_org_id)
    remaining = count_graph_nodes(scratch_org_id)
    if remaining:
        raise RuntimeError(
            f"Scratch graph {scratch_org_id!r} still contains {remaining} node(s) after deletion"
        )
    return deleted


async def load_documents(
    source_org_id: str,
    kb_slugs: Sequence[str],
    limit: int,
) -> list[dict[str, Any]]:
    async with cross_org_admin_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, kb_slug, path, content_type, created_at,
                   extra ->> 'document_text' AS document_text
            FROM knowledge.artifacts
            WHERE org_id = $1
              AND kb_slug = ANY($2::text[])
              AND COALESCE(extra ->> 'document_text', '') <> ''
            ORDER BY created_at, id
            LIMIT $3
            """,
            source_org_id,
            list(kb_slugs),
            limit,
        )
    return [dict(row) for row in rows]


async def replay_documents(
    documents: Sequence[dict[str, Any]],
    scratch_org_id: str,
) -> tuple[int, Counter[str]]:
    replayed = 0
    skipped: Counter[str] = Counter()
    for row in documents:
        document_text = str(row["document_text"])
        skip_reason = graph_episode_skip_reason(document_text)
        if skip_reason:
            skipped[skip_reason] += 1
            continue

        entity_graph_data = EntityGraphData()
        for episode_text in split_episode_text(document_text):
            episode_id = await ingest_episode(
                artifact_id=str(row["id"]),
                document_text=episode_text,
                org_id=scratch_org_id,
                content_type=row["content_type"] or "text",
                belief_time_start=row["created_at"] or int(time.time()),
                kb_slug=row["kb_slug"] or "",
                path=row["path"] or "",
                entity_graph_data=entity_graph_data,
            )
            if episode_id is None:
                raise RuntimeError(f"Graph extraction failed for artifact {row['id']}")
        replayed += 1
    return replayed, skipped


def read_graph_facts(scratch_org_id: str) -> list[str]:
    graph = _open_graph(scratch_org_id)
    result = graph.query(
        "MATCH (:Entity)-[r:RELATES_TO]->(:Entity) "
        "WHERE r.group_id = $org_id RETURN r.fact AS fact",
        params={"org_id": scratch_org_id},
    )
    return [str(row[0]) for row in result.result_set if row and row[0]]


def _marker_count(facts: Sequence[str], marker: str) -> int:
    return sum(marker in f" {fact.lower()} " for fact in facts)


def print_summary(
    facts: Sequence[str],
    selected_documents: int,
    replayed_documents: int,
    skipped: Counter[str],
) -> None:
    unique_facts = len(set(facts))
    rows: list[tuple[str, str, int]] = [
        ("documents", "selected", selected_documents),
        ("documents", "replayed", replayed_documents),
        ("documents", "skipped", sum(skipped.values())),
        *(("skip reason", reason, count) for reason, count in sorted(skipped.items())),
        ("facts", "total", len(facts)),
        ("facts", "unique", unique_facts),
        ("facts", "duplicates", len(facts) - unique_facts),
        *(
            ("meta facts containing marker", marker, _marker_count(facts, marker))
            for marker in META_MARKERS
        ),
        *(
            ("language facts containing marker", repr(marker), _marker_count(facts, marker))
            for marker in LANGUAGE_MARKERS
        ),
    ]
    category_width = max(len("category"), *(len(category) for category, _, _ in rows))
    metric_width = max(len("metric"), *(len(metric) for _, metric, _ in rows))
    print(f"{'category':<{category_width}}  {'metric':<{metric_width}}  count")
    print(f"{'-' * category_width}  {'-' * metric_width}  -----")
    for category, metric, count in rows:
        print(f"{category:<{category_width}}  {metric:<{metric_width}}  {count}")


async def run(
    source_org_id: str,
    kb_slugs: Sequence[str],
    limit: int,
    scratch_org_id: str,
) -> int:
    validate_probe_ids(source_org_id, scratch_org_id)
    if limit <= 0:
        raise ValueError("--limit must be positive")
    if not kb_slugs:
        raise ValueError("At least one --kb-slug is required")

    try:
        if not settings.graphiti_enabled:
            raise RuntimeError("Graphiti is disabled; extraction cannot be measured")
        documents = await load_documents(source_org_id, kb_slugs, limit)
        if not documents:
            raise RuntimeError("No stored document_text found for the requested source and KBs")
        replayed, skipped = await replay_documents(documents, scratch_org_id)
        facts = await asyncio.to_thread(read_graph_facts, scratch_org_id)
        print_summary(facts, len(documents), replayed, skipped)
        return 0
    finally:
        try:
            deleted = await asyncio.to_thread(delete_scratch_graph, scratch_org_id)
            print(
                f"Deleted scratch graph {scratch_org_id!r} ({deleted} node(s)); verification passed"
            )
        except Exception as exc:
            raise RuntimeError(
                f"Cleanup failed: scratch graph {scratch_org_id!r} may have been left behind"
            ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-org-id", required=True)
    parser.add_argument("--kb-slug", action="append", required=True, dest="kb_slugs")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--scratch-org-id", required=True)
    args = parser.parse_args()
    return asyncio.run(run(args.source_org_id, args.kb_slugs, args.limit, args.scratch_org_id))


if __name__ == "__main__":
    raise SystemExit(main())
