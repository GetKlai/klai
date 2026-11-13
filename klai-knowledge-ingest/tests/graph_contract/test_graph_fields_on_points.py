"""Once both products of a version have ended, its points carry the graph fields.

The enrichment job replaces a version's points and the graph job writes the
graph fields onto them, and since the graph has its own worker lane either can
finish last. Real Postgres (the artifact rows), a real FalkorDB (the episodes and
the entities they mention) and Qdrant's in-process engine for the points; only
the model work is replaced.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, VectorParams

from knowledge_ingest import contextual, enrichment_tasks, qdrant_store, rebuild_tasks
from knowledge_ingest import graph as graph_module
from knowledge_ingest.derived_state import set_product_state
from tests.graph_contract.conftest import Contract, seed_graph

_TEXT = "The handset-source pairs with the handset-target over the local network."
_ENTITIES = {"handset-source", "handset-target"}
_GRAPH_FIELDS = ("entity_uuids", "entity_names", "entity_pagerank_max")


class _App:
    def __init__(self) -> None:
        self.tasks: dict = {}

    def task(self, **_kwargs):
        def _decorator(fn):
            self.tasks[fn.__name__] = fn
            return fn

        return _decorator


@pytest.fixture
async def store(graph_helpers: Contract, tenant_rows, monkeypatch, real_procrastinate):
    conn, insert = tenant_rows

    @asynccontextmanager
    async def _conn(*_args):
        yield conn

    for module in (enrichment_tasks, rebuild_tasks):
        monkeypatch.setattr(module, "tenant_scoped_connection", _conn)
    monkeypatch.setattr(enrichment_tasks, "cross_org_admin_connection", _conn)
    client = AsyncQdrantClient(location=":memory:")
    await client.create_collection(
        qdrant_store.COLLECTION,
        vectors_config={
            name: VectorParams(size=2, distance=Distance.COSINE)
            for name in ("vector_chunk", "vector_questions")
        },
    )
    monkeypatch.setattr(qdrant_store, "get_client", lambda: client)
    monkeypatch.setattr(
        enrichment_tasks.sparse_embedder,
        "embed_sparse_batch",
        AsyncMock(side_effect=lambda t: [None] * len(t)),
    )
    app = _App()
    enrichment_tasks._register_tasks(app)
    org_id = graph_helpers.group_id

    async def version() -> str:
        """A new version as the ingest route leaves it: raw points, both products pending."""
        artifact = await insert({"document_text": _TEXT}, active=True)
        for product in ("enrichment", "graph"):
            await set_product_state(conn, artifact, product, "pending")
        await qdrant_store.upsert_chunks(org_id, "docs", "page", [_TEXT], [[0.1, 0.2]], artifact)
        return artifact

    async def graph(artifact: str) -> None:
        async def _extract(**kwargs):
            # What the real ingest_episode does: store the episode and add the
            # entities it mentions to the document's collected data.
            episode_id = f"ep-{uuid.uuid4().hex[:8]}"
            await seed_graph(graph_helpers, [episode_id], {"handset": [episode_id]})
            kwargs["entity_graph_data"].extend(sorted(_ENTITIES), sorted(_ENTITIES))
            return episode_id

        monkeypatch.setattr(enrichment_tasks, "split_episode_text", lambda _t: ["one"])
        monkeypatch.setattr(graph_module, "ingest_episode", _extract)
        await app.tasks["ingest_graphiti_episode"](
            None,
            artifact_id=artifact,
            org_id=org_id,
            content_type="kb_article",
            belief_time_start=1,
            kb_slug="docs",
            path="page",
            replace_stale=True,
        )

    @asynccontextmanager
    async def _model_work():
        async def _chunks(**kw):
            return [enrichment_tasks.enrichment.EnrichedChunk(c, c, "", []) for c in kw["chunks"]]

        with (
            patch.object(enrichment_tasks.enrichment, "enrich_chunks", _chunks),
            patch.object(contextual, "generate_document_summary", AsyncMock(return_value="S.")),
            patch.object(
                enrichment_tasks.embedder,
                "embed",
                AsyncMock(side_effect=lambda t: [[0.1, 0.2]] * len(t)),
            ),
            patch.object(
                enrichment_tasks.kb_config, "get_kb_visibility", AsyncMock(return_value="internal")
            ),
            patch.object(enrichment_tasks.kb_config, "get_kb_aliases", AsyncMock(return_value=[])),
            patch.object(
                enrichment_tasks.kb_config, "get_kb_product_rules", AsyncMock(return_value=[])
            ),
        ):
            yield

    async def enrich(artifact: str) -> None:
        async with _model_work():
            await app.tasks["enrich_document_bulk"](None, artifact_id=artifact)

    async def rebuild(artifact: str) -> str:
        row = dict(await conn.fetchrow("SELECT * FROM knowledge.artifacts WHERE id = $1", artifact))
        async with _model_work():
            return await rebuild_tasks._rebuild_artifact(
                org_id=org_id, kb_slug="docs", artifact=row, semaphore=asyncio.Semaphore(1)
            )

    async def points(artifact: str) -> list[dict]:
        found, _ = await client.scroll(
            qdrant_store.COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="artifact_id", match=MatchValue(value=artifact))]
            ),
            limit=100,
            with_payload=True,
        )
        assert found, "the version has no points"
        return [p.payload or {} for p in found]

    return SimpleNamespace(
        version=version, graph=graph, enrich=enrich, rebuild=rebuild, points=points
    )


def _carry_the_graph(payloads: list[dict]) -> bool:
    return all(
        set(p.get("entity_uuids") or []) == _ENTITIES
        and set(p.get("entity_names") or []) == _ENTITIES
        and "entity_pagerank_max" in p
        for p in payloads
    )


async def test_enrichment_ending_after_the_graph_keeps_the_graph_fields(store) -> None:
    artifact = await store.version()

    await store.graph(artifact)
    await store.enrich(artifact)

    assert _carry_the_graph(await store.points(artifact))


async def test_the_graph_ending_after_enrichment_writes_the_graph_fields(store) -> None:
    artifact = await store.version()

    await store.enrich(artifact)
    await store.graph(artifact)

    assert _carry_the_graph(await store.points(artifact))


async def test_a_rebuild_of_a_version_whose_graph_is_done_keeps_the_graph_fields(store) -> None:
    artifact = await store.version()
    await store.enrich(artifact)
    await store.graph(artifact)

    assert await store.rebuild(artifact) == "processed"

    assert _carry_the_graph(await store.points(artifact))


async def test_a_version_whose_graph_is_not_done_gets_no_graph_fields(store) -> None:
    artifact = await store.version()

    await store.enrich(artifact)

    payloads = await store.points(artifact)
    assert [p for p in payloads if any(f in p for f in _GRAPH_FIELDS)] == []
