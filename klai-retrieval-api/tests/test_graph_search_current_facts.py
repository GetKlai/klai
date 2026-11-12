"""The graph leg only returns facts that still hold, proven against a real FalkorDB.

Graphiti keeps a contradicted fact with ``expired_at`` set and a fact that ended
with ``invalid_at`` set. ``graph_search.search`` drops both after graphiti's
search, because a ``SearchFilters`` filter would switch off the ANN candidate
search. That relies on graphiti-core reading both fields back from FalkorDB onto
every returned edge, so the check runs the real search path with the compat
patches retrieval-api applies.

Opt in like the knowledge-ingest graph contract: point
``GRAPH_CONTRACT_FALKORDB_PORT`` (and optionally ``GRAPH_CONTRACT_FALKORDB_HOST``)
at a disposable FalkorDB; ``GRAPH_CONTRACT_REQUIRED=1`` turns a missing server
into a failure instead of a skip.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import math
import os
import random
import types
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from graphiti_core import Graphiti
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.nodes import EntityNode

from retrieval_api.services import graph_search

_HOST = os.environ.get("GRAPH_CONTRACT_FALKORDB_HOST", "127.0.0.1")
_PORT = os.environ.get("GRAPH_CONTRACT_FALKORDB_PORT")
_DIM = 1024
_QUERY = "harbour tariff"

# The current-facts contract below was last proven against this graphiti-core.
# A bump must re-run this file against FalkorDB before the pin moves with it.
_PROVEN_GRAPHITI_CORE = "0.31.2"

needs_falkordb = pytest.mark.skipif(
    _PORT is None and os.environ.get("GRAPH_CONTRACT_REQUIRED") != "1",
    reason="needs a disposable FalkorDB: set GRAPH_CONTRACT_FALKORDB_PORT",
)


def test_current_facts_contract_was_proven_on_the_installed_graphiti_core() -> None:
    assert importlib.metadata.version("graphiti-core") == _PROVEN_GRAPHITI_CORE, (
        "graphiti-core changed: run tests/test_graph_search_current_facts.py against FalkorDB "
        "(GRAPH_CONTRACT_FALKORDB_PORT) and only then update _PROVEN_GRAPHITI_CORE"
    )


def _vector(text: str) -> list[float]:
    """Unit vector seeded by the lower-cased text: equal text, equal vector."""
    seed = int.from_bytes(hashlib.sha256(text.lower().encode()).digest()[:8], "big")
    rng = random.Random(seed)  # noqa: S311 - deterministic test vector, not security
    raw = [rng.gauss(0.0, 1.0) for _ in range(_DIM)]
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


class _Embedder(EmbedderClient):
    async def create(self, input_data: Any) -> list[float]:
        return _vector(input_data if isinstance(input_data, str) else str(input_data))

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        return [_vector(text) for text in input_data_list]


class _CrossEncoder(CrossEncoderClient):
    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(passage, 1.0 / (index + 1)) for index, passage in enumerate(passages)]


@pytest.fixture
async def graph(monkeypatch: pytest.MonkeyPatch):
    assert _PORT is not None
    monkeypatch.setenv("GRAPHITI_TELEMETRY_ENABLED", "false")
    graphiti = Graphiti(
        graph_driver=FalkorDriver(host=_HOST, port=int(_PORT)),
        llm_client=OpenAIGenericClient(config=LLMConfig(api_key="unused", model="unused")),
        embedder=_Embedder(),
        cross_encoder=_CrossEncoder(),
    )
    org_id = f"currentfacts{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(graph_search.settings, "graphiti_enabled", True)
    monkeypatch.setattr(graph_search, "_graphiti_client", graphiti)
    monkeypatch.setattr(graph_search, "_circuit", graph_search._GraphCircuit())
    driver = graphiti.driver.clone(database=org_id)
    await driver.build_indices_and_constraints()
    try:
        yield driver, org_id
    finally:
        await driver.execute_query("MATCH (n) DETACH DELETE n")
        await graphiti.close()


async def _fact(driver: Any, org_id: str, fact: str, embed: str, **times: datetime) -> None:
    now = datetime.now(UTC)
    source = EntityNode(name=f"{fact} source", group_id=org_id, labels=["Entity"], created_at=now)
    target = EntityNode(name=f"{fact} target", group_id=org_id, labels=["Entity"], created_at=now)
    for node in (source, target):
        node.name_embedding = _vector(node.name)
        await node.save(driver)
    edge = EntityEdge(
        group_id=org_id,
        source_node_uuid=source.uuid,
        target_node_uuid=target.uuid,
        name="HAS_TARIFF",
        fact=fact,
        fact_embedding=_vector(embed),
        episodes=[],
        created_at=now,
        **times,
    )
    await edge.save(driver)


async def _seed(driver: Any, org_id: str) -> set[str]:
    """12 current facts, 4 that no longer hold; the 4 outrank the rest on both legs
    (shorter text for full-text, the query's own vector for similarity)."""
    past = datetime.now(UTC) - timedelta(days=1)
    future = datetime.now(UTC) + timedelta(days=30)
    current = {f"the {_QUERY} for berth {n} is in force for every container" for n in range(11)}
    for fact in sorted(current):
        await _fact(driver, org_id, fact, embed=fact)
    ending = f"the {_QUERY} for the east quay is in force until next month"
    await _fact(driver, org_id, ending, embed=ending, invalid_at=future)
    for n in range(2):
        await _fact(driver, org_id, f"{_QUERY} old {n}", embed=_QUERY, expired_at=past)
        await _fact(driver, org_id, f"{_QUERY} ended {n}", embed=_QUERY, invalid_at=past)
    return current | {ending}


@needs_falkordb
async def test_expired_facts_are_dropped_and_the_result_count_kept(graph) -> None:
    driver, org_id = graph
    current = await _seed(driver, org_id)

    results = await graph_search.search(_QUERY, org_id, top_k=20)

    texts = [result["text"] for result in results]
    assert len(texts) == graph_search.DEFAULT_SEARCH_LIMIT
    assert set(texts) <= current


@needs_falkordb
async def test_concurrent_searches_each_get_the_full_count(graph) -> None:
    driver, org_id = graph
    current = await _seed(driver, org_id)

    answers = await asyncio.gather(
        *(graph_search.search(_QUERY, org_id, top_k=top_k) for top_k in (20, 5, 20, 3, 20, 10))
    )

    assert [len(answer) for answer in answers] == [10, 5, 10, 3, 10, 10]
    assert all({result["text"] for result in answer} <= current for answer in answers)


@needs_falkordb
async def test_a_search_past_its_timeout_still_returns_nothing(graph, monkeypatch) -> None:
    driver, org_id = graph
    await _seed(driver, org_id)
    monkeypatch.setattr(graph_search.settings, "graph_search_timeout", 1e-6)

    assert await graph_search.search(_QUERY, org_id, top_k=20) == []


def test_a_naive_timestamp_counts_as_utc() -> None:
    now = datetime.now(UTC)
    naive_past = (now - timedelta(hours=1)).replace(tzinfo=None)
    naive_future = (now + timedelta(hours=1)).replace(tzinfo=None)

    edges = [
        types.SimpleNamespace(expired_at=naive_past, invalid_at=None),
        types.SimpleNamespace(expired_at=None, invalid_at=naive_future),
    ]

    assert graph_search._current_facts(edges) == [edges[1]]
