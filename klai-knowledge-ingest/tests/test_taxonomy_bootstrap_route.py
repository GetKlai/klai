from __future__ import annotations

from qdrant_client.models import FieldCondition, IsEmptyCondition


def test_bootstrap_filter_scans_all_chunks_for_initial_taxonomy():
    from knowledge_ingest.routes.taxonomy import _build_bootstrap_scroll_filter

    scroll_filter = _build_bootstrap_scroll_filter(
        "org-1",
        "support",
        only_untagged=False,
    )

    assert len(scroll_filter.must or []) == 2
    assert all(isinstance(condition, FieldCondition) for condition in scroll_filter.must or [])


def test_bootstrap_filter_scans_only_untagged_when_taxonomy_exists():
    from knowledge_ingest.routes.taxonomy import _build_bootstrap_scroll_filter

    scroll_filter = _build_bootstrap_scroll_filter(
        "org-1",
        "support",
        only_untagged=True,
    )

    must = scroll_filter.must or []
    assert len(must) == 3
    assert any(isinstance(condition, IsEmptyCondition) for condition in must)
    empty_condition = next(condition for condition in must if isinstance(condition, IsEmptyCondition))
    assert empty_condition.is_empty.key == "taxonomy_node_ids"
