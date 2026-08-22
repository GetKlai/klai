"""Rename existing Graphiti episodes from artifact-version ids to document keys.

SPEC-RAG-GRAPH-CITE-002 changed episode naming from ``artifact_id`` to
``doc:<kb_slug>:<path>``. That fixes new ingests, but NOT the graph that is
already there — and it cannot heal by itself: knowledge-ingest dedups on
content_hash, so re-crawling an unchanged page never creates a new episode.
Without this backfill every fact extracted before the change keeps a pointer
to a superseded artifact version, its Qdrant lookup misses, and its citation
renders as a truncated sentence instead of a link.

The rename is metadata only. It does NOT re-extract, makes no LLM calls, and
draws nothing from the shared klai-fast rate limit that enrichment and
taxonomy compete over. Postgres already holds the mapping: every successful
episode writes ``graphiti_episode_id`` into ``knowledge.artifacts.extra``.

Superseded artifacts are included on purpose. Their episodes carry exactly the
stale edges this is meant to heal, and their ``kb_slug``/``path`` still name
the document correctly.

Usage (in a knowledge-ingest container):

    docker exec klai-core-knowledge-ingest-1 \
        python -m scripts.backfill_episode_document_names --org-id <org_id> [--dry-run]

Idempotent: SET writes the same name on a re-run, so an interrupted pass can
simply be repeated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict

import structlog
from klai_kb_slugs import episode_name

from knowledge_ingest import graph as graph_module
from knowledge_ingest.config import settings
from knowledge_ingest.db import tenant_scoped_connection

logger = structlog.get_logger()


async def collect_renames(org_id: str) -> tuple[dict[str, list[str]], int]:
    """Return (renames, skipped) for one org.

    ``renames`` maps the stable document name to the episode uuids that should
    carry it; ``skipped`` counts artifacts with no usable episode pointer.
    """
    renames: dict[str, list[str]] = defaultdict(list)
    skipped = 0

    async with tenant_scoped_connection(org_id) as conn:
        rows = await conn.fetch(
            """
            SELECT kb_slug, path, extra
            FROM knowledge.artifacts
            WHERE org_id = $1 AND extra IS NOT NULL
            ORDER BY created_at
            """,
            org_id,
        )

    for row in rows:
        try:
            extra = json.loads(row["extra"]) if isinstance(row["extra"], str) else row["extra"]
        except (TypeError, ValueError):
            continue
        episode_id = (extra or {}).get("graphiti_episode_id")
        # "no-chunks" is the sentinel backfill.py writes for artifacts it
        # deliberately skipped; it is not an episode uuid.
        if not episode_id or episode_id == "no-chunks":
            skipped += 1
            continue
        kb_slug, path = row["kb_slug"], row["path"]
        if not kb_slug or not path:
            skipped += 1
            continue
        name = episode_name(kb_slug, path)
        renames[name].append(episode_id)

    return dict(renames), skipped


async def run(org_id: str, dry_run: bool) -> int:
    if not settings.graphiti_enabled:
        logger.error("graphiti_disabled", org_id=org_id)
        return 1

    renames, skipped = await collect_renames(org_id)
    episodes = sum(len(uuids) for uuids in renames.values())
    logger.info(
        "backfill_episode_names_plan",
        org_id=org_id,
        documents=len(renames),
        episodes=episodes,
        skipped_without_episode=skipped,
        dry_run=dry_run,
    )
    if dry_run or not renames:
        return 0

    renamed = await graph_module.rename_episodes_to_document_keys(org_id, renames)
    logger.info(
        "backfill_episode_names_complete",
        org_id=org_id,
        documents=len(renames),
        episodes_matched=renamed,
        episodes_expected=episodes,
    )
    # A shortfall is normal: an episode row can be gone from FalkorDB while
    # its artifact still records the id (deleted KB, purged connector). Say so
    # rather than let the difference pass silently.
    if renamed < episodes:
        logger.warning(
            "backfill_episode_names_shortfall",
            org_id=org_id,
            missing_in_graph=episodes - renamed,
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True, help="Zitadel org id (one tenant per run)")
    parser.add_argument("--dry-run", action="store_true", help="Report the plan, change nothing")
    args = parser.parse_args()
    return asyncio.run(run(args.org_id, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
