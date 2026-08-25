"""REQ-5 contract tests for connector document cleanup by source_ref."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import qdrant_store


def test_delete_endpoint_cleans_every_store_in_retry_safe_order(client) -> None:
    """The scoped endpoint closes PG state, clears external stores, then hard-deletes PG."""
    conn = MagicMock()
    steps: list[str] = []

    @asynccontextmanager
    async def _tenant_connection(org_id: str):
        assert org_id == "org-verified"
        yield conn

    async def _soft_delete(*_args, **_kwargs):
        steps.append("soft_delete")
        return 1

    async def _list_ids(*_args, **_kwargs):
        steps.append("list_ids")
        return ["artifact-1"]

    async def _episodes(*_args, **_kwargs):
        steps.append("episodes")
        return ["episode-1"]

    async def _graph(*_args, **_kwargs):
        steps.append("graph")

    async def _qdrant(*_args, **_kwargs):
        steps.append("qdrant")

    async def _hard_delete(*_args, **_kwargs):
        steps.append("hard_delete")
        return 1

    with (
        patch(
            "knowledge_ingest.routes.ingest.assert_caller_identity_tenant_only",
            new_callable=AsyncMock,
        ) as identity,
        patch("knowledge_ingest.routes.ingest.tenant_scoped_connection", _tenant_connection),
        patch(
            "knowledge_ingest.routes.ingest.pg_store.soft_delete_connector_artifacts_by_source_ref",
            side_effect=_soft_delete,
        ) as soft_delete,
        patch(
            "knowledge_ingest.routes.ingest.pg_store.list_connector_artifact_ids_by_source_ref",
            side_effect=_list_ids,
        ) as list_ids,
        patch(
            "knowledge_ingest.routes.ingest.pg_store.get_episode_ids_for_document_history",
            side_effect=_episodes,
        ),
        patch(
            "knowledge_ingest.routes.ingest.graph_module.delete_kb_episodes",
            side_effect=_graph,
        ) as delete_graph,
        patch(
            "knowledge_ingest.routes.ingest.qdrant_store.delete_connector_document",
            side_effect=_qdrant,
        ) as delete_qdrant,
        patch(
            "knowledge_ingest.routes.ingest.pg_store.delete_connector_artifacts_by_source_ref",
            side_effect=_hard_delete,
        ) as hard_delete,
    ):
        identity.return_value = "org-verified"
        response = client.delete(
            "/ingest/v1/connector/document",
            params={
                "org_id": "org-1",
                "kb_slug": "prices",
                "connector_id": "connector-1",
                "source_ref": "json-feed:connector-1:group-a",
            },
            headers={"X-Caller-Service": "connector"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "artifacts_deleted": 1,
        "episodes_deleted": 1,
    }
    identity.assert_awaited_once()
    assert identity.await_args.kwargs == {"claimed_org_id": "org-1"}
    scope = (conn, "org-verified", "prices", "connector-1", "json-feed:connector-1:group-a")
    soft_delete.assert_awaited_once_with(*scope)
    list_ids.assert_awaited_once_with(*scope)
    delete_graph.assert_awaited_once_with("org-verified", ["episode-1"])
    delete_qdrant.assert_awaited_once_with(
        "org-verified", "prices", "connector-1", "json-feed:connector-1:group-a"
    )
    hard_delete.assert_awaited_once_with(*scope)
    assert steps == ["soft_delete", "list_ids", "episodes", "graph", "qdrant", "hard_delete"]


def test_delete_endpoint_requires_internal_secret(client) -> None:
    response = client.delete(
        "/ingest/v1/connector/document",
        params={
            "org_id": "org-1",
            "kb_slug": "prices",
            "connector_id": "connector-1",
            "source_ref": "json-feed:connector-1:group-a",
        },
        headers={"X-Internal-Secret": "wrong-secret", "X-Caller-Service": "connector"},
    )

    assert response.status_code == 401
    assert "X-Internal-Secret" in response.json()["detail"]


@pytest.mark.asyncio
async def test_qdrant_delete_is_org_kb_connector_and_source_ref_scoped(monkeypatch) -> None:
    qdrant = MagicMock()
    qdrant.delete = AsyncMock()
    monkeypatch.setattr(qdrant_store, "get_client", lambda: qdrant)

    await qdrant_store.delete_connector_document(
        "org-1", "prices", "connector-1", "json-feed:connector-1:group-a"
    )

    selector = qdrant.delete.await_args.kwargs["points_selector"]
    conditions = {
        condition.key: condition.match.value for condition in selector.must
    }
    assert conditions == {
        "org_id": "org-1",
        "kb_slug": "prices",
        "source_connector_id": "connector-1",
        "source_ref": "json-feed:connector-1:group-a",
    }
