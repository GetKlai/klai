"""Operator script: one form for recorded episodes, and the graph fields back on the points.

Rows written before mid-2026 record the episode of a finished extraction as one
id in ``graphiti_episode_id``; later rows use the ``graphiti_episode_ids`` list.
``_link_graph_fields``, which puts the graph fields back after the enrichment
job replaced a version's points, reads only the list, so re-enriching a
single-id version leaves all its points without ``entity_uuids``. This script
does two things, per organisation:

1. For every row (current and replaced) whose graph is ``done`` and whose
   episode is recorded only as a real single id, write that id as a
   one-element ``graphiti_episode_ids``. The scalar key stays; sentinels
   (``no-chunks``, ``skipped:<reason>``) are not ids and are left alone.
2. For every current version whose graph is ``done``, that has points and of
   which no point carries ``entity_uuids``, call ``_link_graph_fields``.

    # Preview (default): counts per organisation. Writes nothing.
    python -m knowledge_ingest.scripts.relink_graph_fields [--org <org_id>]

    # Write. Needs INGEST_OK=1; holds the organisation's operation lock.
    INGEST_OK=1 python -m knowledge_ingest.scripts.relink_graph_fields \\
        [--org <org_id>] --execute

A finished graph whose episodes mention no entity legitimately has no fields;
it is told apart by what ``load_entity_graph_data`` finds for the recorded
episodes, which is exactly what ``_link_graph_fields`` would write, and counted
as ``no_entities`` (or ``episodes_missing`` when the graph lacks a recorded
episode, which ``reextract_document_graph`` repairs). Neither is linked, so
neither shows up as ``not_linked``. Every write is conditional on the state
the script read, so a second run finds nothing to do and an interrupted run
continues where it stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from typing import Any

import asyncpg
import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import FieldCondition, Filter, IsEmptyCondition, MatchValue, PayloadField

from knowledge_ingest import graph as graph_module
from knowledge_ingest.db import cross_org_admin_connection
from knowledge_ingest.derived_state import DONE, product_state
from knowledge_ingest.enrichment_tasks import _link_graph_fields
from knowledge_ingest.operations import guard_cli, kb_operation
from knowledge_ingest.pg_store import _SENTINEL, PURGE_PENDING
from knowledge_ingest.qdrant_store import COLLECTION, get_client

logger = structlog.get_logger()

_SCROLL = 1000
KEYS = (
    "ids_to_list",
    "missing_fields",
    "episodes_in_graph",
    "no_entities",
    "episodes_missing",
    "to_link",
    "linked",
    "not_linked",
)


def _single_id(extra: dict) -> str | None:
    """The real episode id of a row recorded only in the single-id form."""
    ids = extra.get("graphiti_episode_ids")
    scalar = extra.get("graphiti_episode_id")
    if (isinstance(ids, list) and ids) or not isinstance(scalar, str) or not scalar:
        return None
    if scalar == "no-chunks" or scalar.startswith("skipped:"):
        return None
    return scalar


def _episodes(extra: dict) -> list[str]:
    """The episodes ``_link_graph_fields`` reads, once a single id is in the list form."""
    ids = extra.get("graphiti_episode_ids")
    if isinstance(ids, list) and ids:
        return [str(i) for i in ids[int(extra.get("graphiti_episode_ids_from") or 0) :]]
    single = _single_id(extra)
    return [single] if single else []


async def _point_counts(client: AsyncQdrantClient, org_id: str) -> tuple[Counter, Counter]:
    """Per artifact of the org: its points, and those that carry ``entity_uuids``."""
    org = FieldCondition(key="org_id", match=MatchValue(value=org_id))
    linked = IsEmptyCondition(is_empty=PayloadField(key="entity_uuids"))
    counts: list[Counter] = []
    for selection in (Filter(must=[org]), Filter(must=[org], must_not=[linked])):
        counter: Counter = Counter()
        offset = None
        while True:
            points, offset = await client.scroll(
                COLLECTION,
                scroll_filter=selection,
                limit=_SCROLL,
                offset=offset,
                with_payload=["artifact_id"],
                with_vectors=False,
            )
            counter.update(str((p.payload or {}).get("artifact_id")) for p in points)
            if offset is None:
                break
        counts.append(counter)
    return counts[0], counts[1]


async def _has_fields(client: AsyncQdrantClient, org_id: str, artifact_id: str) -> bool:
    result = await client.count(
        COLLECTION,
        count_filter=Filter(
            must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="artifact_id", match=MatchValue(value=artifact_id)),
            ],
            must_not=[IsEmptyCondition(is_empty=PayloadField(key="entity_uuids"))],
        ),
        exact=True,
    )
    return result.count > 0


async def _rows(conn: asyncpg.Connection, org_id: str, *, current_only: bool) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT id::text AS id, (extra - 'document_text')::text AS extra
        FROM knowledge.artifacts
        WHERE org_id = $1
          AND (NOT $2 OR (belief_time_end = $3 AND index_status <> $4))
          AND (extra ? 'graphiti_episode_id' OR extra ? 'graphiti_episode_ids')
        ORDER BY id
        """,
        org_id,
        current_only,
        _SENTINEL,
        PURGE_PENDING,
    )
    done = [{"id": r["id"], "extra": json.loads(r["extra"] or "{}")} for r in rows]
    return [r for r in done if product_state(r["extra"], "graph").state == DONE]


async def _move_to_list(conn: asyncpg.Connection, artifact_id: str, episode_id: str) -> None:
    # Only while the row still holds this single id and no list: an extraction
    # finishing meanwhile writes its own list, and that one wins.
    await conn.execute(
        """
        UPDATE knowledge.artifacts
        SET extra = extra || jsonb_build_object('graphiti_episode_ids', jsonb_build_array($2::text))
        WHERE id = $1::uuid AND extra->>'graphiti_episode_id' = $2
          AND COALESCE(jsonb_array_length(
                CASE WHEN jsonb_typeof(extra->'graphiti_episode_ids') = 'array'
                     THEN extra->'graphiti_episode_ids' END), 0) = 0
        """,
        artifact_id,
        episode_id,
    )


async def relink_org(
    conn: asyncpg.Connection, client: AsyncQdrantClient, org_id: str, *, execute: bool
) -> Counter:
    """Do, or without ``execute`` only count, both parts for one organisation."""
    counts: Counter = Counter({key: 0 for key in KEYS})
    for row in await _rows(conn, org_id, current_only=False):
        if single := _single_id(row["extra"]):
            counts["ids_to_list"] += 1
            if execute:
                await _move_to_list(conn, row["id"], single)

    points, with_fields = await _point_counts(client, org_id)
    missing = [
        row
        for row in await _rows(conn, org_id, current_only=True)
        if points[row["id"]] and not with_fields[row["id"]]
    ]
    stored = await graph_module.stored_episode_uuids(org_id) if missing else set()
    for row in missing:
        counts["missing_fields"] += 1
        episodes = _episodes(row["extra"])
        in_graph = bool(episodes) and set(episodes) <= stored
        counts["episodes_in_graph"] += in_graph
        found = graph_module.EntityGraphData()
        if episodes:
            await graph_module.load_entity_graph_data(org_id, episodes, found)
        if not found.entity_uuids and not found.entity_names:
            counts["no_entities" if in_graph else "episodes_missing"] += 1
            continue
        if not execute:
            counts["to_link"] += 1
            continue
        await _link_graph_fields(org_id, row["id"])
        counts["linked" if await _has_fields(client, org_id, row["id"]) else "not_linked"] += 1
    logger.info(
        "graph_fields_relinked" if execute else "graph_fields_relink_preview",
        org_id=org_id,
        **counts,
    )
    return counts


async def run(org_id: str | None, *, execute: bool) -> dict[str, Counter]:
    async with cross_org_admin_connection() as conn:
        orgs = (
            [org_id]
            if org_id
            else [
                r["org_id"]
                for r in await conn.fetch(
                    "SELECT DISTINCT org_id FROM knowledge.artifacts "
                    "WHERE extra ? 'graphiti_episode_id' OR extra ? 'graphiti_episode_ids' "
                    "ORDER BY org_id"
                )
            ]
        )
        per_org: dict[str, Counter] = {}
        for org in orgs:
            async with kb_operation(org, None, execute=execute):
                per_org[org] = await relink_org(conn, get_client(), org, execute=execute)
        return per_org


def _print(per_org: dict[str, Any], execute: bool) -> None:
    print(f"{'org':40} " + " ".join(f"{key:>17}" for key in KEYS))
    for org, counts in per_org.items():
        print(f"{org:40} " + " ".join(f"{counts[key]:>17}" for key in KEYS))
    print("written." if execute else "preview; nothing written.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--org", help="one organisation (default: every organisation)")
    parser.add_argument("--execute", action="store_true", help="write; preview without it")
    args = parser.parse_args()
    guard_cli("relink_graph_fields", args.execute)
    _print(asyncio.run(run(args.org, execute=args.execute)), args.execute)


if __name__ == "__main__":
    main()
