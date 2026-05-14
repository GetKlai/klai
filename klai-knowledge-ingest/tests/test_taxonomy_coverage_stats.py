from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _point(taxonomy_node_ids=()):
    point = MagicMock()
    payload = {}
    if taxonomy_node_ids is not None:
        payload["taxonomy_node_ids"] = taxonomy_node_ids
    point.payload = payload
    return point


@pytest.mark.asyncio
async def test_coverage_stats_scrolls_once_per_page_instead_of_counting_per_node():
    from knowledge_ingest.routes.taxonomy import _build_coverage_stats_from_qdrant

    client = MagicMock()
    client.scroll = AsyncMock(
        side_effect=[
            (
                [
                    _point([1, 2]),
                    _point([]),
                    _point(None),
                    _point(["2", "bad", 2]),
                    _point([99]),
                ],
                "next",
            ),
            ([_point([1, 1, 3])], None),
        ]
    )
    client.count = AsyncMock()

    response = await _build_coverage_stats_from_qdrant(
        client=client,
        org_id="org-1",
        kb_slug="support",
        taxonomy_nodes=[
            SimpleNamespace(id=1),
            SimpleNamespace(id=2),
            SimpleNamespace(id=3),
        ],
    )

    assert response.total_chunks == 6
    assert response.untagged_count == 2
    assert {node.taxonomy_node_id: node.chunk_count for node in response.nodes} == {
        1: 2,
        2: 2,
        3: 1,
    }
    assert client.scroll.await_count == 2
    client.count.assert_not_called()
