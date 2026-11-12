"""Count, and only with --execute remove, the graph content that no longer counts.

Postgres keeps every version of a document; the graph should hold the facts
of one: the newest version that was actually extracted
(``pg_store.get_alive_episode_uuids_for_org``). Before ingest replaced earlier
extractions, one tenant's graph held 1,748 episodes of current versions next
to 8,442 of replaced versions and 442 of removed documents, and 14,620 of its
204,498 fact edges cited no episode that still existed. Retrieval searches
those edges, so the stale facts kept reaching answers.

By default this only reads and prints counts. ``--execute`` removes the same
selection through ``graph.delete_kb_episodes`` (bounded batches; an edge goes
when no remaining episode backs it) and prints the counts again. It removes
every episode that is not alive, and strips uuids of episodes that no longer
exist from the edges citing them. It refuses to execute when that would leave
a document that has graph content now without any.

Run it by hand only, one tenant per run (in a knowledge-ingest container):

    docker exec klai-core-knowledge-ingest-1 \\
        python -m scripts.prune_stale_graph --org <zitadel_org_id> [--execute]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass

from knowledge_ingest import graph as graph_module
from knowledge_ingest import pg_store
from knowledge_ingest.config import settings
from knowledge_ingest.db import tenant_scoped_connection

# FalkorDB caps a result at RESULTSET_SIZE rows (10,000 in production), so edges
# are read as one collected row per id range. 20,000 edges per range read in
# ~120 ms on a synthetic 200k-edge graph, far inside the 5000 ms query timeout.
_EDGE_PAGE = 20_000

_ROWS_SQL = """
SELECT a.id::text AS id,
       a.superseded_by::text AS superseded_by,
       a.belief_time_end = $2 AS active,
       ARRAY(
           SELECT episode_id
           FROM jsonb_array_elements_text(
               CASE
                   WHEN a.extra::jsonb ? 'graphiti_episode_ids'
                       THEN a.extra::jsonb->'graphiti_episode_ids'
                   WHEN a.extra::jsonb->>'graphiti_episode_id' IS NOT NULL
                       THEN jsonb_build_array(a.extra::jsonb->>'graphiti_episode_id')
                   ELSE '[]'::jsonb
               END
           ) AS stored_episode(episode_id)
           WHERE episode_id <> 'no-chunks' AND episode_id NOT LIKE 'skipped:%'
       ) AS episode_ids
FROM knowledge.artifacts AS a
WHERE a.org_id = $1
"""


@dataclass(frozen=True)
class Row:
    id: str
    superseded_by: str | None
    active: bool
    episode_ids: tuple[str, ...]


@dataclass(frozen=True)
class Snapshot:
    rows: list[Row]
    alive: set[str]
    episodes: set[str]
    edges: list[list[str]]


async def _graph_state(org_id: str) -> tuple[set[str], list[list[str]]]:
    driver = graph_module._get_graphiti().driver.clone(org_id)
    episodes = await graph_module.stored_episode_uuids(org_id)
    records, _, _ = await driver.execute_query(
        "MATCH ()-[r:RELATES_TO]->() RETURN max(id(r)) AS top"
    )
    top = records[0]["top"]
    edges: list[list[str]] = []
    for low in range(0, (top if top is not None else -1) + 1, _EDGE_PAGE):
        records, _, _ = await driver.execute_query(
            "MATCH ()-[r:RELATES_TO]->() WHERE id(r) >= $low AND id(r) < $high "
            "RETURN collect(r.episodes) AS episodes",
            low=low,
            high=low + _EDGE_PAGE,
        )
        edges.extend(list(cited or []) for cited in records[0]["episodes"])
    return episodes, edges


async def load_snapshot(org_id: str) -> Snapshot:
    # Graph first, Postgres second: an extraction writes its episode to the
    # graph before it records the uuid on its row, so this order keeps an
    # episode written meanwhile from looking stale.
    episodes, edges = await _graph_state(org_id)
    async with tenant_scoped_connection(org_id) as conn:
        records = await conn.fetch(_ROWS_SQL, org_id, pg_store._SENTINEL)
        alive = await pg_store.get_alive_episode_uuids_for_org(conn, org_id)
    rows = [Row(r["id"], r["superseded_by"], r["active"], tuple(r["episode_ids"])) for r in records]
    return Snapshot(rows=rows, alive=alive, episodes=episodes, edges=edges)


def selection(snapshot: Snapshot) -> set[str]:
    """Every stored episode that is not alive, plus every missing uuid an edge still cites."""
    cited = {uuid for edge in snapshot.edges for uuid in edge}
    return (snapshot.episodes - snapshot.alive) | (cited - snapshot.episodes)


def _histories(rows: list[Row]) -> dict[str, set[str]]:
    """Active row id -> episode ids of that document's every version."""
    by_id = {row.id: row for row in rows}
    predecessors: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if row.superseded_by:
            predecessors[row.superseded_by].append(row.id)
    histories: dict[str, set[str]] = {}
    for row in rows:
        if not row.active:
            continue
        seen, todo, ids = {row.id}, [row.id], set()
        while todo:
            current = todo.pop()
            ids.update(by_id[current].episode_ids)
            for older in predecessors[current]:
                if older not in seen:
                    seen.add(older)
                    todo.append(older)
        histories[row.id] = ids
    return histories


def report(snapshot: Snapshot) -> tuple[dict[str, int], list[str]]:
    """Counts of what the selection removes, and the documents it would empty."""
    selected = selection(snapshot)
    replaced, referenced = set(), set()
    for row in snapshot.rows:
        referenced.update(row.episode_ids)
        if not row.active and row.superseded_by:
            replaced.update(row.episode_ids)
    stale = snapshot.episodes - snapshot.alive
    counts = {
        "episodes_in_graph": len(snapshot.episodes),
        "episodes_alive_in_graph": len(snapshot.episodes & snapshot.alive),
        "episodes_removed_replaced_version": len(stale & replaced),
        "episodes_removed_removed_document": len((stale & referenced) - replaced),
        "episodes_removed_no_row": len(stale - referenced),
        "edges_in_graph": len(snapshot.edges),
        "edges_removed": 0,
        "edges_kept_with_shorter_episode_list": 0,
        "edges_kept_unchanged": 0,
        "edges_without_existing_episode": 0,
        "edges_without_any_episode_left_alone": 0,
    }
    for cited in snapshot.edges:
        if not cited:
            counts["edges_without_any_episode_left_alone"] += 1
            counts["edges_kept_unchanged"] += 1
            continue
        if not any(uuid in snapshot.episodes for uuid in cited):
            counts["edges_without_existing_episode"] += 1
        removed = sum(uuid in selected for uuid in cited)
        if removed == len(cited):
            counts["edges_removed"] += 1
        elif removed:
            counts["edges_kept_with_shorter_episode_list"] += 1
        else:
            counts["edges_kept_unchanged"] += 1
    counts["edges_kept"] = counts["edges_in_graph"] - counts["edges_removed"]

    emptied = [
        document
        for document, history in sorted(_histories(snapshot.rows).items())
        if history & snapshot.episodes and not history & snapshot.episodes & snapshot.alive
    ]
    counts["documents_left_without_episode"] = len(emptied)
    return counts, emptied


def _print(title: str, counts: dict[str, int]) -> None:
    print(title)
    for key, value in counts.items():
        print(f"  {key}: {value}")


async def run(org_id: str, execute: bool) -> int:
    if not settings.graphiti_enabled:
        print("graphiti is disabled; nothing to do")
        return 1
    snapshot = await load_snapshot(org_id)
    counts, emptied = report(snapshot)
    _print(f"org {org_id}: {'before' if execute else 'dry run'}", counts)
    if emptied:
        # By construction the rule keeps each document's newest extracted
        # version. A document lands here when that version's row lists episode
        # ids the graph does not hold while an older version's episodes are
        # still there, so applying the rule would empty it.
        print(
            "REFUSING: the alive rule (newest extracted version per document) keeps "
            "episode ids that are not in the graph for these documents, so pruning "
            "would remove their only graph content:"
        )
        for document in emptied:
            print(f"  active artifact {document}")
        return 2
    if not execute:
        return 0

    async with tenant_scoped_connection(org_id) as conn:
        alive_now = await pg_store.get_alive_episode_uuids_for_org(conn, org_id)
    await graph_module.delete_kb_episodes(org_id, sorted(selection(snapshot) - alive_now))
    after, _ = report(await load_snapshot(org_id))
    _print(f"org {org_id}: after", after)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True, help="Zitadel org id (one tenant per run)")
    parser.add_argument(
        "--execute", action="store_true", help="Remove the selection; without it only counts"
    )
    args = parser.parse_args()
    return asyncio.run(run(args.org, args.execute))


if __name__ == "__main__":
    sys.exit(main())
