"""Graphiti's write and read paths against a real FalkorDB.

The fake LLM, embedder and reranker are deterministic, so what varies is the
set of entity names and what graphiti sends to FalkorDB because of them. After
every step the server must still answer and the stored nodes must be queryable.
See conftest.py for how the target server is chosen.
"""

from __future__ import annotations

import itertools

import pytest
from graphiti_core.driver.falkordb import STOPWORDS

from tests.graph_contract.conftest import Contract
from tests.graph_contract.corpus import build_corpus

CORPUS = build_corpus()

# retrieval-api's graph leg drops queries without any alphanumeric character
# before they reach graphiti (graph_search._has_searchable_text).
_SEARCH_ONLY_NAMES = [
    "the of and",
    "a",
    "NOT",
    "what is the price of the product",
    "foo | bar",
    "@name:value",
    '"unclosed quote',
    "(unbalanced",
    "wild*card",
    "dash-ed query",
]


def _searchable(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


@pytest.mark.parametrize("category", sorted(CORPUS))
async def test_names_survive_write_dedup_and_read(contract: Contract, category: str) -> None:
    names = CORPUS[category]

    await contract.ingest(f"{category}: first add_episode", names)
    await contract.expect_entities(f"{category}: nodes queryable after first add_episode", names)

    # The second episode finds the stored nodes through graphiti's exact-name
    # lookup and semantic candidate search, the queries behind the 2026-10-23 outage.
    await contract.ingest(f"{category}: second add_episode against stored nodes", names)
    await contract.expect_entities(f"{category}: nodes queryable after second add_episode", names)

    edges = await contract.step(f"{category}: read edges", contract.stored_edges())
    if any(a.strip() and b.strip() for a, b in itertools.pairwise(names)):
        assert edges, f"{category}: no edge was stored, so edge dedup and resolution never ran"

    for name in names:
        if _searchable(name):
            await contract.step(
                f"{category}: search {name[:40]!r}",
                contract.graphiti.search(name, group_ids=[contract.group_id]),
            )
            await contract.step(
                f"{category}: search_ (nodes, edges, episodes) {name[:40]!r}",
                contract.graphiti.search_(name, group_ids=[contract.group_id]),
            )


async def test_whole_corpus_in_one_tenant_graph(contract: Contract) -> None:
    everything = [name for names in CORPUS.values() for name in names]

    await contract.ingest("whole corpus: add_episode", everything)
    await contract.expect_entities("whole corpus: nodes queryable", everything)
    await contract.ingest("whole corpus: add_episode again", everything)
    await contract.expect_entities("whole corpus: nodes queryable again", everything)

    for query in _SEARCH_ONLY_NAMES + list(STOPWORDS) + [" ".join(STOPWORDS)]:
        await contract.step(
            f"whole corpus: search {query[:40]!r}",
            contract.graphiti.search(query, group_ids=[contract.group_id]),
        )
