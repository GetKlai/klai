"""``derived_state.product_state`` on rows written before the state existed.

Each case is an ``extra`` shape the code has written; the reader must say what
it actually knows, and ``unknown`` where the old fields cannot tell.
"""

from __future__ import annotations

import pytest

from knowledge_ingest.derived_state import ProductState, product_state

GRAPH_CASES = {
    "nothing recorded": ({}, ProductState("unknown")),
    "held out of the graph": (
        {"graphiti_episode_id": "skipped:navigation_page"},
        ProductState("not_applicable", "navigation_page"),
    ),
    "refresh held it out": (
        {
            "graphiti_episode_ids": [],
            "graphiti_episode_complete": True,
            "graphiti_episode_id": "skipped:navigation_page",
        },
        ProductState("not_applicable", "navigation_page"),
    ),
    "no text (backfill)": (
        {"graphiti_episode_id": "no-chunks", "graphiti_episode_ids": []},
        ProductState("not_applicable", "no_text"),
    ),
    "legacy scalar only": ({"graphiti_episode_id": "ep-1"}, ProductState("done")),
    "list without the complete flag": ({"graphiti_episode_ids": ["ep-1"]}, ProductState("done")),
    "complete": (
        {"graphiti_episode_ids": ["ep-1"], "graphiti_episode_complete": True},
        ProductState("done"),
    ),
    "running or failed partway": (
        {"graphiti_episode_ids": ["ep-1"], "graphiti_episode_complete": False},
        ProductState("pending"),
    ),
    "retries exhausted": (
        {
            "graphiti_episode_ids": ["ep-1"],
            "graphiti_episode_complete": False,
            "graphiti_exhausted_at": 1,
        },
        ProductState("failed", "retries_exhausted"),
    ),
    "quarantined": (
        {"graphiti_episode_ids": [], "graphiti_quarantined_at": 1},
        ProductState("failed", "quarantined"),
    ),
    "new state wins over old fields": (
        {"graph_state": "failed", "graph_reason": "no_stored_text", "graphiti_episode_id": "ep"},
        ProductState("failed", "no_stored_text"),
    ),
}


@pytest.mark.parametrize("case", sorted(GRAPH_CASES))
def test_graph_state_of_old_rows(case: str) -> None:
    extra, expected = GRAPH_CASES[case]
    assert product_state(extra, "graph") == expected


ENRICHMENT_CASES = {
    "connector row: nothing was ever recorded": (
        {"source_connector_id": "c1"},
        "synced",
        ProductState("unknown"),
    ),
    "upload synced: done or skipped, the old field cannot tell": (
        {},
        "synced",
        ProductState("unknown"),
    ),
    "upload pending": ({}, "pending", ProductState("pending")),
    "upload failed": ({}, "failed", ProductState("failed", "legacy")),
    "skipped by policy at ingest": (
        {"llm_enrichment_skipped": True},
        "synced",
        ProductState("not_applicable", "policy"),
    ),
    "new state": (
        {"enrichment_state": "done", "source_connector_id": "c1"},
        "synced",
        ProductState("done"),
    ),
}


@pytest.mark.parametrize("case", sorted(ENRICHMENT_CASES))
def test_enrichment_state_of_old_rows(case: str) -> None:
    extra, index_status, expected = ENRICHMENT_CASES[case]
    assert product_state(extra, "enrichment", index_status=index_status) == expected
