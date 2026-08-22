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

Superseded artifacts are included on purpose: their episodes carry exactly the
stale edges this is meant to heal. They are keyed on the path of the ACTIVE
version at the end of their ``superseded_by`` chain, not their own — a renamed
connector page (see #1172) leaves old rows under a path Qdrant no longer holds,
and naming an episode after that is no better than the artifact_id it replaced.

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

    The name comes from the CURRENT version of each document, reached by
    following ``superseded_by``, so episodes extracted from a page that was
    later renamed land on the key retrieval can actually resolve.
    """
    renames: dict[str, list[str]] = defaultdict(list)
    skipped = 0

    async with tenant_scoped_connection(org_id) as conn:
        # Walk superseded_by to the END of each supersession chain and take
        # THAT row's kb_slug/path. A row's own path is the wrong answer when
        # the document was renamed: the old path names a document that no
        # longer exists in Qdrant, so an episode keyed on it stays exactly as
        # unresolvable as the artifact_id it replaced. The active version's
        # path is the one retrieval can look up.
        #
        # Only rows with superseded_by IS NULL are real chain ends. Taking the
        # deepest row reached would treat the depth cap as a terminus: a chain
        # longer than the bound would silently key its episodes on an
        # intermediate version's path, which is the same unresolvable-name
        # defect this script exists to remove. An origin whose walk finds no
        # terminal LEFT JOINs to NULL and lands in ``skipped`` instead, so it
        # is reported rather than guessed at.
        #
        # depth < 20 bounds the walk. superseded_by points forward in time so
        # a cycle should be impossible, but an unbounded recursive CTE that
        # meets one hangs against production instead of failing. Production
        # (Voys, 2026-08-22) tops out at 7.
        rows = await conn.fetch(
            """
            WITH RECURSIVE chain AS (
                SELECT a.id AS origin, a.superseded_by, a.kb_slug, a.path, 0 AS depth
                FROM knowledge.artifacts a
                WHERE a.org_id = $1 AND a.extra IS NOT NULL

                UNION ALL

                SELECT c.origin, n.superseded_by, n.kb_slug, n.path, c.depth + 1
                FROM chain c
                JOIN knowledge.artifacts n ON n.id = c.superseded_by AND n.org_id = $1
                WHERE c.superseded_by IS NOT NULL AND c.depth < 20
            )
            terminal AS (
                SELECT DISTINCT ON (c.origin) c.origin, c.kb_slug, c.path
                FROM chain c
                WHERE c.superseded_by IS NULL
                ORDER BY c.origin
            )
            SELECT t.kb_slug, t.path, a.extra
            FROM knowledge.artifacts a
            LEFT JOIN terminal t ON t.origin = a.id
            WHERE a.org_id = $1 AND a.extra IS NOT NULL
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
