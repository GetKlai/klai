from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest


def _point(point_id: str, payload: dict):
    point = MagicMock()
    point.id = point_id
    point.payload = payload
    return point


@pytest.mark.asyncio
async def test_remove_taxonomy_node_from_qdrant_clears_deleted_id_only():
    from knowledge_ingest.routes.taxonomy import _remove_taxonomy_node_from_qdrant

    client = MagicMock()
    client.scroll = AsyncMock(
        side_effect=[
            (
                [
                    _point("p1", {"taxonomy_node_id": 5, "taxonomy_node_ids": [5, 7]}),
                    _point("p2", {"taxonomy_node_ids": [5]}),
                    _point("p3", {"taxonomy_node_ids": [7]}),
                ],
                "next",
            ),
            (
                [
                    _point("p4", {"taxonomy_node_id": "5"}),
                ],
                None,
            ),
        ]
    )
    client.set_payload = AsyncMock()
    client.delete_payload = AsyncMock()

    updated = await _remove_taxonomy_node_from_qdrant(
        client=client,
        org_id="org-1",
        kb_slug="support",
        node_id=5,
        batch_size=100,
    )

    assert updated == 3
    assert client.scroll.await_count == 2
    client.set_payload.assert_has_awaits(
        [
            call(
                "klai_knowledge",
                payload={"taxonomy_node_ids": [7]},
                points=["p1"],
            ),
            call(
                "klai_knowledge",
                payload={"taxonomy_node_ids": []},
                points=["p2"],
            ),
        ]
    )
    client.delete_payload.assert_has_awaits(
        [
            call("klai_knowledge", keys=["taxonomy_node_id"], points=["p1"]),
            call("klai_knowledge", keys=["taxonomy_node_id"], points=["p4"]),
        ]
    )
