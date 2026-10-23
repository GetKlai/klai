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
from tests.graph_contract.corpus import build_corpus, normalise

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
            await contract.expect_search_hit(f"{category}: {name[:40]!r}", name)


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


# stored name -> (name the second episode extracts, whether the LLM says it is the same entity)
_NEAR_DUPLICATES = {
    "Meridian Logistics": ("Meridian Logistics Holdings", True),
    "Harbor Freight Co": ("Harbor Freight Company", True),
    "Quillon Labs": ("Quillon Labs Academy", False),
}


async def test_llm_resolved_duplicates_keep_the_stored_node(contract: Contract) -> None:
    """Near-duplicates fail the exact and fuzzy match, so only the LLM step can merge them."""
    stored_names = [*_NEAR_DUPLICATES, "Orchard Row"]
    for stored, (extracted, _) in _NEAR_DUPLICATES.items():
        contract.embedder.similar[extracted] = stored
    contract.llm.duplicates = {
        extracted: stored for stored, (extracted, same) in _NEAR_DUPLICATES.items() if same
    }

    await contract.ingest("dedup: first add_episode", stored_names)
    before = {normalise(n.name): n.uuid for n in await contract.stored_nodes()}
    assert len(before) == len(stored_names)

    # "meridian logistics" is an exact-name hit after normalising, resolved without the LLM.
    second = ["meridian  logistics", *(extracted for extracted, _ in _NEAR_DUPLICATES.values())]
    await contract.ingest("dedup: second add_episode", second)

    sent_to_llm = {name for sent, _ in contract.llm.dedupe_prompts for name in sent}
    assert sent_to_llm >= {extracted for extracted, _ in _NEAR_DUPLICATES.values()}, (
        "the near-duplicates never reached _resolve_with_llm, so the LLM dedup path is untested; "
        f"the dedupe prompt saw {sorted(sent_to_llm)!r}"
    )

    after = {normalise(n.name): n.uuid for n in await contract.stored_nodes()}
    for stored, (extracted, same) in _NEAR_DUPLICATES.items():
        assert after[normalise(stored)] == before[normalise(stored)], f"{stored!r} was replaced"
        if same:
            assert normalise(extracted) not in after, f"{extracted!r} became a second node"
        else:
            assert normalise(extracted) in after, f"{extracted!r} was merged though the LLM said no"
    assert len(after) == len(stored_names) + 1  # only the distinct "Quillon Labs Academy" is new


async def test_search_finds_own_tenant_and_never_another(contract: Contract) -> None:
    other = contract.other_tenant()
    mine = ["Meridian Logistics", "Harbor Freight Co", "Orchard Row", "the of and", "Café Zoë"]
    theirs = ["Zephyr Mills", "Lantern Quay", "Orchard Row"]

    await contract.ingest("tenancy: own add_episode", mine)
    await other.ingest("tenancy: other add_episode", theirs)
    own_nodes = {n.uuid: n for n in await contract.stored_nodes()}
    other_nodes = {n.uuid: n for n in await other.stored_nodes()}
    own_edges = {e.uuid for e in await contract.stored_edges()}
    other_edges = {e.uuid for e in await other.stored_edges()}
    assert own_nodes.keys().isdisjoint(other_nodes), "tenants share a node uuid"

    for name in mine:
        edges = await contract.step(
            f"tenancy: search {name!r}",
            contract.graphiti.search(name, group_ids=[contract.group_id]),
        )
        combined = await contract.step(
            f"tenancy: search_ {name!r}",
            contract.graphiti.search_(name, group_ids=[contract.group_id]),
        )
        assert any(n.uuid in own_nodes for n in combined.nodes), (
            f"search_ {name!r} returned no node of this tenant's graph"
        )
        returned_nodes = {n.uuid for n in combined.nodes}
        returned_edges = {e.uuid for e in combined.edges} | {e.uuid for e in edges}
        assert returned_nodes <= own_nodes.keys(), f"search_ {name!r} leaked another tenant's node"
        assert returned_edges <= own_edges, f"search {name!r} returned an edge from another graph"
        assert not returned_edges & other_edges
        assert {n.group_id for n in combined.nodes} <= {contract.group_id}

    # Only the other tenant knows these names; they must not turn up here.
    for name in ("Zephyr Mills", "Lantern Quay"):
        combined = await contract.step(
            f"tenancy: search_ other tenant's {name!r}",
            contract.graphiti.search_(name, group_ids=[contract.group_id]),
        )
        assert not {n.uuid for n in combined.nodes} & other_nodes.keys()
        assert all(n.name not in {"Zephyr Mills", "Lantern Quay"} for n in combined.nodes)
