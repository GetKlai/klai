"""Count, and only with --execute remove, the graph content that no longer counts.

Postgres keeps every version of a document; the graph should hold the facts
of one: its newest finished extraction
(``pg_store.get_current_graph_episode_uuids_for_org``). Before ingest replaced
earlier extractions, one tenant's graph held 1,748 episodes of current versions
next to 8,442 of replaced versions and 442 of removed documents, and 14,620 of
its 204,498 fact edges cited no episode that still existed. Retrieval searches
those edges, so the stale facts kept reaching answers.

By default this only reads and prints counts. ``--execute`` removes the same
selection through ``graph.delete_kb_episodes`` (bounded batches; an edge goes
when no remaining episode backs it), deletes the edges that list no existing
episode at all, and prints the counts again. An episode is removed only when it
is not current graph content AND its document either has a finished current
extraction or no active version at all. A document is its ``superseded_by``
chain, its knowledge base and path, and its connector and source_ref together:
rows closed before the link existed, or whose link was cleared on purpose, have
no chain, and judging them alone would delete the only facts of a document
whose new version is not extracted yet. Episodes younger than
``graph.ORPHAN_GRACE`` are never removed. The script refuses to execute when a
document that has graph content now would be left without any.

This is the only path that removes replaced versions' episodes: the org-wide
sweep after a connector purge keeps every episode any row records, so the
owner approves this removal per run, after seeing the dry-run counts.

Run it by hand only, one tenant per run (in a knowledge-ingest container):

    docker exec klai-core-knowledge-ingest-1 \\
        python -m scripts.prune_stale_graph --org <zitadel_org_id> [--execute]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from knowledge_ingest import graph as graph_module
from knowledge_ingest import pg_store
from knowledge_ingest.config import settings
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.operations import guard_cli, kb_operation

# FalkorDB caps a result at RESULTSET_SIZE rows (10,000 in production), so edges
# are read as one collected row per id range. 20,000 edges per range read in
# ~120 ms on a synthetic 200k-edge graph, far inside the 5000 ms query timeout.
_EDGE_PAGE = 20_000


@dataclass(frozen=True)
class Snapshot:
    rows: list
    episodes: set[str]
    old_episodes: set[str]
    edges: list[list[str]]


async def _edges(org_id: str) -> list[list[str]]:
    driver = graph_module._get_graphiti().driver.clone(org_id)
    records, _, _ = await driver.execute_query(
        "MATCH ()-[r:RELATES_TO]->() RETURN max(id(r)) AS top"
    )
    top = records[0]["top"]
    edges: list[list[str]] = []
    for low in range(0, (top if top is not None else -1) + 1, _EDGE_PAGE):
        # coalesce: collect() drops nulls, and an edge without an episodes
        # property is exactly an edge no episode backs.
        records, _, _ = await driver.execute_query(
            "MATCH ()-[r:RELATES_TO]->() WHERE id(r) >= $low AND id(r) < $high "
            "RETURN collect(coalesce(r.episodes, [])) AS episodes",
            low=low,
            high=low + _EDGE_PAGE,
        )
        edges.extend(list(cited) for cited in records[0]["episodes"])
    return edges


async def load_snapshot(org_id: str) -> Snapshot:
    # Edges, then episodes, then Postgres: an extraction writes its episode,
    # then its edges, then records the uuid on its row. In this order an
    # episode written meanwhile is either absent from the edge read (so its
    # facts are never taken for unbacked) or recorded by the time Postgres is
    # read; ORPHAN_GRACE covers a job that has not recorded it yet.
    edges = await _edges(org_id)
    cutoff = datetime.now(UTC) - graph_module.ORPHAN_GRACE
    episodes = await graph_module.stored_episode_uuids(org_id)
    old_episodes = await graph_module.stored_episode_uuids(org_id, older_than=cutoff)
    async with tenant_scoped_connection(org_id) as conn:
        rows = await pg_store.get_graph_rows_for_org(conn, org_id)
    return Snapshot(rows=rows, episodes=episodes, old_episodes=old_episodes, edges=edges)


def _documents(rows: list) -> list[list]:
    """Rows grouped per document: linked by superseded_by, kb+path, or connector+source_ref."""
    parent = {row["id"]: row["id"] for row in rows}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    first: dict[tuple, str] = {}
    for row in rows:
        if row["superseded_by"] in parent:
            union(row["id"], row["superseded_by"])
        keys = [("path", row["kb_slug"], row["path"])]
        if row["source_connector_id"] and row["source_ref"]:
            keys.append(("source", row["source_connector_id"], row["source_ref"]))
        for key in keys:
            if key in first:
                union(row["id"], first[key])
            else:
                first[key] = row["id"]
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(find(row["id"]), []).append(row)
    return list(groups.values())


@dataclass(frozen=True)
class Plan:
    selection: set[str]
    counts: dict[str, int]
    emptied: list[str]


def plan(snapshot: Snapshot) -> Plan:
    extractions = pg_store.current_extractions(snapshot.rows)
    current = {episode_id for ids, _ in extractions.values() for episode_id in ids}
    replaced: set[str] = set()
    removed: set[str] = set()
    protected: set[str] = set()
    emptied: list[str] = []
    for document in _documents(snapshot.rows):
        ids = {episode_id for row in document for episode_id in row["episode_ids"]}
        active = [extractions[row["id"]] for row in document if row["active"]]
        if not active:
            removed |= ids
        elif all(finished for _, finished in active):
            replaced |= ids
            # Content now, none after, and not because the newest version was
            # finished with nothing (held out of the graph, or no text).
            keeps = {episode_id for kept, _ in active for episode_id in kept}
            if ids & snapshot.episodes and not keeps & snapshot.episodes and keeps:
                emptied.extend(row["id"] for row in document if row["active"])
        else:
            protected |= ids
    stale = snapshot.old_episodes - current
    cited = {uuid for edge in snapshot.edges for uuid in edge}
    selection = (stale - protected) | (cited - snapshot.episodes)

    counts = {
        "episodes_in_graph": len(snapshot.episodes),
        "episodes_current": len(snapshot.episodes & current),
        "episodes_removed_replaced_version": len(stale & replaced - protected),
        "episodes_removed_removed_document": len(stale & removed - replaced - protected),
        "episodes_removed_recorded_by_no_row": len(stale - replaced - removed - protected),
        "episodes_kept_document_not_finished_extracting": len(stale & protected),
        "episodes_kept_too_recent": len(snapshot.episodes - snapshot.old_episodes - current),
        "edges_in_graph": len(snapshot.edges),
        "edges_removed": 0,
        "edges_removed_without_existing_episode": 0,
        "edges_kept_with_shorter_episode_list": 0,
        "edges_kept_unchanged": 0,
    }
    for edge in snapshot.edges:
        if not any(uuid in snapshot.episodes for uuid in edge):
            counts["edges_removed_without_existing_episode"] += 1
            counts["edges_removed"] += 1
            continue
        removed_here = sum(uuid in selection for uuid in edge)
        if removed_here == len(edge):
            counts["edges_removed"] += 1
        elif removed_here:
            counts["edges_kept_with_shorter_episode_list"] += 1
        else:
            counts["edges_kept_unchanged"] += 1
    counts["edges_kept"] = counts["edges_in_graph"] - counts["edges_removed"]
    counts["documents_left_without_episode"] = len(emptied)
    return Plan(selection=selection, counts=counts, emptied=sorted(emptied))


def _print(title: str, counts: dict[str, int]) -> None:
    print(title)
    for key, value in counts.items():
        print(f"  {key}: {value}")


async def run(org_id: str, execute: bool) -> int:
    if not settings.graphiti_enabled:
        print("graphiti is disabled; nothing to do")
        return 1
    before = plan(await load_snapshot(org_id))
    _print(f"org {org_id}: {'before' if execute else 'dry run'}", before.counts)
    if before.emptied:
        # The current version's row lists episode ids the graph does not hold
        # while an older version's episodes are still there, so the rule would
        # remove the document's only graph content.
        print(
            "REFUSING: the current-content rule (newest finished extraction per document) "
            "keeps episode ids that are not in the graph for these documents, so pruning "
            "would remove their only graph content:"
        )
        for document in before.emptied:
            print(f"  active artifact {document}")
        return 2
    if not execute:
        return 0

    async with tenant_scoped_connection(org_id) as conn:
        current_now = await pg_store.get_current_graph_episode_uuids_for_org(conn, org_id)
    await graph_module.delete_kb_episodes(org_id, sorted(before.selection - current_now))
    await graph_module.delete_unbacked_edges(org_id)
    _print(f"org {org_id}: after", plan(await load_snapshot(org_id)).counts)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True, help="Zitadel org id (one tenant per run)")
    parser.add_argument(
        "--execute", action="store_true", help="Remove the selection; without it only counts"
    )
    args = parser.parse_args()
    guard_cli("prune_stale_graph", args.execute)
    return asyncio.run(_run_locked(args.org, args.execute))


async def _run_locked(org_id: str, execute: bool) -> int:
    async with kb_operation(org_id, None, execute=execute):
        return await run(org_id, execute)


if __name__ == "__main__":
    sys.exit(main())
