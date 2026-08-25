"""Report whether a graph rebuild is complete, from both sides.

A rebuild replaces a tenant's episodes with freshly extracted ones. "Complete"
is easy to believe and hard to establish: the obvious check is whether the
backfill has documents left, and that check passes while stale episodes sit in
the graph and while documents silently have none.

Five checks, and the last two are the ones that matter, because they look in
opposite directions. Checking only that every episode resolves to a document
misses everything MISSING; checking only that every document has an episode
misses everything LEFT OVER. That asymmetry is not hypothetical: the #1148
rebuild cleaned episodes by walking ``artifacts.extra->>'graphiti_episode_id'``,
and 109 episodes whose artifact link had been lost were never on the list. They
survived the cleanup and kept serving stale facts, invisible to any check
driven from Postgres.

So the stale-episode check is driven from the GRAPH, on episode creation date.
What is in the graph is in the graph, whether or not Postgres remembers it.

Usage:

    docker exec klai-core-knowledge-ingest-1 python -m scripts.verify_graph_rebuild \
        --org-id <org_id> --since 2026-08-24 --kb-slug support --kb-slug sip

Read-only. Exits 0 when complete, 1 when not, so it can gate a rebuild script.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog
from klai_kb_slugs import parse_episode_name

from knowledge_ingest import graph as graph_module
from knowledge_ingest.config import settings
from knowledge_ingest.db import cross_org_admin_connection

logger = structlog.get_logger()

_ACTIVE = 253402300800


async def _graph_episodes(org_id: str) -> list[tuple[str, str]]:
    """Return (name, day) for every episode in the tenant's graph."""
    graphiti = graph_module._get_graphiti()
    driver = graphiti.driver.clone(org_id)
    result = await driver.execute_query(
        "MATCH (e:Episodic) RETURN e.name AS name, left(toString(e.created_at),10) AS day"
    )
    records = result[0] if isinstance(result, tuple) else result
    return [(r["name"], r["day"]) for r in records if r["name"]]


async def _orphan_edge_count(org_id: str) -> int:
    """Edges whose every episode is gone: facts with no provenance left."""
    graphiti = graph_module._get_graphiti()
    driver = graphiti.driver.clone(org_id)
    result = await driver.execute_query(
        "MATCH (e:Episodic) WITH collect(e.uuid) AS alive "
        "MATCH ()-[r:RELATES_TO]->() WHERE NOT any(x IN r.episodes WHERE x IN alive) "
        "RETURN count(r) AS n"
    )
    records = result[0] if isinstance(result, tuple) else result
    return int(records[0]["n"]) if records else 0


async def verify(org_id: str, since: str, kb_slugs: list[str]) -> dict[str, int]:
    episodes = await _graph_episodes(org_id)
    scoped = [
        (name, day)
        for name, day in episodes
        if (parsed := parse_episode_name(name)) and parsed[0] in kb_slugs
    ]
    stale = [(n, d) for n, d in scoped if d < since]
    fresh_docs = {p for n, d in scoped if d >= since and (p := parse_episode_name(n))}

    orphans = await _orphan_edge_count(org_id)

    async with cross_org_admin_connection() as conn:
        pending = await conn.fetchval(
            """
            SELECT count(*) FROM knowledge.artifacts
            WHERE org_id = $1 AND kb_slug = ANY($2::text[]) AND belief_time_end = $3
              AND NOT (extra ? 'graphiti_episode_id')
            """,
            org_id,
            kb_slugs,
            _ACTIVE,
        )
        # Deliberately skipped documents are not gaps: a navigation page must
        # not become an episode (#1148) and "no-chunks" marks a document with
        # nothing to extract. Without this the check can never report complete.
        rows = await conn.fetch(
            """
            SELECT kb_slug, path FROM knowledge.artifacts
            WHERE org_id = $1 AND kb_slug = ANY($2::text[]) AND belief_time_end = $3
              AND coalesce(extra->>'graphiti_episode_id', '') NOT LIKE 'skipped:%'
              AND coalesce(extra->>'graphiti_episode_id', '') <> 'no-chunks'
            """,
            org_id,
            kb_slugs,
            _ACTIVE,
        )

    live_docs = {(r["kb_slug"], r["path"]) for r in rows}
    missing = live_docs - fresh_docs
    dangling = fresh_docs - live_docs

    counts = {
        "pending_documents": int(pending),
        "stale_episodes": len(stale),
        "orphan_edges": orphans,
        "episodes_without_document": len(dangling),
        "documents_without_episode": len(missing),
    }
    logger.info("graph_rebuild_verified", org_id=org_id, since=since, **counts)
    for kb, path in sorted(missing)[:10]:
        logger.info("graph_rebuild_missing_document", kb_slug=kb, path=path)
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True, help="Zitadel org id")
    parser.add_argument(
        "--since",
        required=True,
        help="YYYY-MM-DD the rebuild started. Episodes older than this are stale.",
    )
    parser.add_argument(
        "--kb-slug",
        action="append",
        dest="kb_slugs",
        required=True,
        help="Knowledge base in scope; repeat for several.",
    )
    args = parser.parse_args()

    if not settings.graphiti_enabled:
        logger.error("graphiti_disabled")
        return 1

    counts = asyncio.run(verify(args.org_id, args.since, args.kb_slugs))
    complete = not any(counts.values())
    logger.info("graph_rebuild_complete" if complete else "graph_rebuild_incomplete", **counts)
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
