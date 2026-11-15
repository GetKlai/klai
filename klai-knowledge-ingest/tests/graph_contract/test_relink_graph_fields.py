"""relink_graph_fields moves single-id episode records into the list form and
puts the graph fields back on the points of finished graphs.

The scenario is the one seen after a catch-up: a version whose finished graph
is recorded only in the old ``graphiti_episode_id`` form is enriched again, and
``_link_graph_fields`` (which reads only the list form) leaves its new points
without the graph fields. Real Postgres, a real FalkorDB and Qdrant's
in-process engine, through the fixtures of ``test_graph_fields_on_points``.
"""

# pytest registers an imported fixture under the name it is bound to, so ``store``
# is imported as itself and every test that takes it redefines that name.
# ruff: noqa: F811

from __future__ import annotations

import json

from qdrant_client.models import FieldCondition, Filter, MatchValue

from knowledge_ingest import qdrant_store
from knowledge_ingest.scripts import relink_graph_fields as script
from tests.graph_contract.conftest import seed_graph
from tests.graph_contract.test_graph_fields_on_points import (  # noqa: F401
    _GRAPH_FIELDS,
    _carry_the_graph,
    store,
)


async def _extra(conn, artifact: str) -> dict:
    return json.loads(
        await conn.fetchval("SELECT extra::text FROM knowledge.artifacts WHERE id = $1", artifact)
    )


async def _single_id_form(conn, artifact: str) -> None:
    """Record the finished extraction as rows written before the list form did."""
    await conn.execute(
        "UPDATE knowledge.artifacts "
        "SET extra = extra - 'graphiti_episode_ids' - 'graphiti_episode_ids_from' "
        "WHERE id = $1",
        artifact,
    )


async def _payloads(artifact: str) -> list[dict]:
    found, _ = await qdrant_store.get_client().scroll(
        qdrant_store.COLLECTION,
        scroll_filter=Filter(
            must=[FieldCondition(key="artifact_id", match=MatchValue(value=artifact))]
        ),
        limit=100,
        with_payload=True,
    )
    return sorted((p.payload or {} for p in found), key=json.dumps)


def _without_fields(payloads: list[dict]) -> bool:
    return bool(payloads) and not any(f in p for p in payloads for f in _GRAPH_FIELDS)


async def _legacy_version_without_fields(store, conn) -> str:
    """A current version with a finished single-id graph, enriched again after it."""
    artifact = await store.version()
    await store.graph(artifact)
    await _single_id_form(conn, artifact)
    await store.enrich(artifact)
    assert _without_fields(await _payloads(artifact))
    return artifact


async def _relink(conn, org_id: str, *, execute: bool):
    return await script.relink_org(conn, qdrant_store.get_client(), org_id, execute=execute)


async def test_a_single_id_graph_gets_the_list_form_and_its_points_the_graph_fields(
    store, tenant_rows, graph_helpers
) -> None:
    conn, _ = tenant_rows
    artifact = await _legacy_version_without_fields(store, conn)
    episode = (await _extra(conn, artifact))["graphiti_episode_id"]

    counts = await _relink(conn, graph_helpers.group_id, execute=True)

    extra = await _extra(conn, artifact)
    assert extra["graphiti_episode_ids"] == [episode]
    assert extra["graphiti_episode_id"] == episode
    assert _carry_the_graph(await store.points(artifact))
    assert counts["ids_to_list"] == 1
    assert counts["linked"] == 1


async def test_a_sentinel_in_the_single_id_key_is_left_alone(
    store, tenant_rows, graph_helpers
) -> None:
    conn, insert = tenant_rows
    sentinels = [
        await insert({"graphiti_episode_id": value, "graph_state": "done"}, active=True, path=value)
        for value in ("no-chunks", "skipped:too_short")
    ]
    before = [await _extra(conn, a) for a in sentinels]

    counts = await _relink(conn, graph_helpers.group_id, execute=True)

    assert [await _extra(conn, a) for a in sentinels] == before
    assert counts["ids_to_list"] == 0


async def test_the_preview_writes_nothing(store, tenant_rows, graph_helpers) -> None:
    conn, _ = tenant_rows
    artifact = await _legacy_version_without_fields(store, conn)
    extra, payloads = await _extra(conn, artifact), await _payloads(artifact)

    counts = await _relink(conn, graph_helpers.group_id, execute=False)

    assert await _extra(conn, artifact) == extra
    assert await _payloads(artifact) == payloads
    assert (counts["ids_to_list"], counts["missing_fields"], counts["episodes_in_graph"]) == (
        1,
        1,
        1,
    )
    assert counts["to_link"] == 1
    assert counts["linked"] == 0


async def test_a_second_run_changes_nothing(store, tenant_rows, graph_helpers) -> None:
    conn, _ = tenant_rows
    artifact = await _legacy_version_without_fields(store, conn)
    await _relink(conn, graph_helpers.group_id, execute=True)
    extra, payloads = await _extra(conn, artifact), await _payloads(artifact)

    counts = await _relink(conn, graph_helpers.group_id, execute=True)

    assert await _extra(conn, artifact) == extra
    assert await _payloads(artifact) == payloads
    assert (counts["ids_to_list"], counts["missing_fields"], counts["linked"]) == (0, 0, 0)


async def test_a_current_version_whose_graph_is_not_done_is_not_touched(
    store, tenant_rows, graph_helpers
) -> None:
    conn, _ = tenant_rows
    artifact = await store.version()
    episode = "ep-unfinished"
    await seed_graph(graph_helpers, [episode], {"pending-fact": [episode]})
    await conn.execute(
        "UPDATE knowledge.artifacts SET extra = extra || $2::jsonb WHERE id = $1",
        artifact,
        json.dumps({"graphiti_episode_id": episode, "graphiti_episode_complete": False}),
    )
    await store.enrich(artifact)
    extra, payloads = await _extra(conn, artifact), await _payloads(artifact)

    counts = await _relink(conn, graph_helpers.group_id, execute=True)

    assert await _extra(conn, artifact) == extra
    assert await _payloads(artifact) == payloads
    assert _without_fields(payloads)
    assert (counts["ids_to_list"], counts["missing_fields"]) == (0, 0)


async def test_a_finished_graph_without_entities_is_counted_apart_from_a_failure(
    store, tenant_rows, graph_helpers
) -> None:
    conn, _ = tenant_rows
    artifact = await store.version()
    episode = "ep-no-entities"
    await seed_graph(graph_helpers, [episode], {})
    await conn.execute(
        "UPDATE knowledge.artifacts SET extra = extra || $2::jsonb WHERE id = $1",
        artifact,
        json.dumps({"graphiti_episode_id": episode, "graph_state": "done"}),
    )
    await store.enrich(artifact)

    counts = await _relink(conn, graph_helpers.group_id, execute=True)

    assert counts["missing_fields"] == 1
    assert counts["no_entities"] == 1
    assert (counts["linked"], counts["not_linked"]) == (0, 0)
    assert _without_fields(await _payloads(artifact))
