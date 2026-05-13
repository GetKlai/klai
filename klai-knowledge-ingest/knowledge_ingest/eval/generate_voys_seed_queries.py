"""Reproduction script for the Voys seed-query suites (SPEC-RAG-EVAL-001 Unit 4).

Pulls a random sample of 50 artifact paths from the Voys `support` KB and prints
them grouped by content_type. Use the output as input when hand-curating a fresh
batch of queries to keep the suites in sync with corpus drift.

This script does NOT auto-write YAML — query curation is a human-review step.
The committed `chat.yaml` and `knowledge_org.yaml` were produced by:

  1. Running this script against prod (via SSH to core-01)
  2. Reading 50 sampled paths grouped by theme
  3. Hand-writing 30 queries per suite per the mix in plan.md §4 Unit 4

Usage (against the dev DB, mirroring prod schema):

  uv run python -m knowledge_ingest.eval.generate_voys_seed_queries

Or against prod (read-only):

  ssh core-01 "docker exec klai-core-postgres-1 psql -U klai -d klai -c \\
    \\"SELECT content_type, path FROM knowledge.artifacts \\
       WHERE org_id = '368884765035593759' \\
         AND belief_time_end > 99999999999 \\
       ORDER BY random() LIMIT 50;\\""

The committed suites carry one query per id; rotating to a fresh corpus snapshot
means rewriting the YAMLs by hand against the new sample. Mark approves each
batch before it lands in main.
"""

from __future__ import annotations

import asyncio

from knowledge_ingest.db import get_pool

VOYS_ORG_ID = "368884765035593759"


async def sample_voys_paths(limit: int = 50) -> list[tuple[str, str]]:
    """Return up to `limit` random (content_type, path) tuples from Voys's support KB."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT content_type, path
        FROM knowledge.artifacts
        WHERE org_id = $1
          AND belief_time_end > 99999999999
        ORDER BY random()
        LIMIT $2
        """,
        VOYS_ORG_ID,
        limit,
    )
    return [(row["content_type"], row["path"]) for row in rows]


async def _main() -> None:
    samples = await sample_voys_paths(50)
    by_type: dict[str, list[str]] = {}
    for content_type, path in samples:
        by_type.setdefault(content_type, []).append(path)
    for content_type, paths in sorted(by_type.items()):
        print(f"\n=== {content_type} ({len(paths)}) ===")
        for path in sorted(paths):
            print(f"  {path}")


if __name__ == "__main__":
    asyncio.run(_main())
