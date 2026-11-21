"""Contract tests for connector document cleanup by source_ref.

What the delete leaves behind in each store is proven against real
PostgreSQL in tests/integration/test_document_purge_postgres.py; these tests
cover the route's auth and the order of the shared purge.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import document_purge


def test_delete_endpoint_purges_every_version_of_the_source_ref(client) -> None:
    conn = MagicMock()

    @asynccontextmanager
    async def _tenant_connection(org_id: str):
        assert org_id == "org-verified"
        yield conn

    with (
        patch(
            "knowledge_ingest.routes.ingest.assert_caller_identity_tenant_only",
            new_callable=AsyncMock,
            return_value="org-verified",
        ) as identity,
        patch("knowledge_ingest.routes.ingest.tenant_scoped_connection", _tenant_connection),
        patch(
            "knowledge_ingest.routes.ingest.pg_store.list_connector_artifact_ids_by_source_ref",
            new_callable=AsyncMock,
            return_value=["artifact-1", "artifact-2"],
        ) as list_ids,
        patch(
            "knowledge_ingest.routes.ingest.document_purge.purge_documents",
            new_callable=AsyncMock,
            return_value=(2, 1),
        ) as purge,
    ):
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
    assert response.json() == {"status": "ok", "artifacts_deleted": 2, "episodes_deleted": 1}
    identity.assert_awaited_once()
    assert identity.await_args is not None
    assert identity.await_args.kwargs == {"claimed_org_id": "org-1"}
    list_ids.assert_awaited_once_with(
        conn, "org-verified", "prices", "connector-1", "json-feed:connector-1:group-a"
    )
    purge.assert_awaited_once_with(conn, "org-verified", ["artifact-1", "artifact-2"])


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


def test_document_delete_by_path_requires_internal_secret(client) -> None:
    response = client.delete(
        "/ingest/v1/document",
        params={"org_id": "org-1", "path": "scribe/txn-1"},
        headers={"X-Internal-Secret": "wrong-secret", "X-Caller-Service": "scribe"},
    )

    assert response.status_code == 401


def _transcript_delete(client, path: str, caller: str):
    conn = MagicMock()

    @asynccontextmanager
    async def _tenant_connection(org_id: str):
        yield conn

    with (
        patch(
            "knowledge_ingest.routes.ingest.assert_caller_identity_tenant_only",
            new_callable=AsyncMock,
            return_value="org-1",
        ),
        patch("knowledge_ingest.routes.ingest.tenant_scoped_connection", _tenant_connection),
        patch(
            "knowledge_ingest.routes.ingest.pg_store.list_document_heads",
            new_callable=AsyncMock,
            return_value=["artifact-1"],
        ) as heads,
        patch(
            "knowledge_ingest.routes.ingest.document_purge.purge_documents",
            new_callable=AsyncMock,
            return_value=(1, 0),
        ) as purge,
    ):
        response = client.delete(
            "/ingest/v1/document",
            params={"org_id": "org-1", "path": path},
            headers={"X-Caller-Service": caller},
        )
    return response, heads, purge


def test_document_delete_removes_a_scribe_transcript_in_every_kb(client) -> None:
    response, heads, purge = _transcript_delete(client, "scribe/txn_0123abcd", "scribe")

    assert response.status_code == 200
    heads.assert_awaited_once_with(ANY, "org-1", "scribe/txn_0123abcd", None)
    purge.assert_awaited_once()


@pytest.mark.parametrize(
    "path", ["file:sha256:abc", "docs/page.md", "scribe/../x", "scribe/", "notes/scribe/txn_1"]
)
def test_document_delete_refuses_anything_but_a_transcript_path(client, path: str) -> None:
    response, _, purge = _transcript_delete(client, path, "scribe")

    assert response.status_code == 422
    purge.assert_not_awaited()


@pytest.mark.parametrize("caller", ["portal-api", "connector", "knowledge-mcp"])
def test_document_delete_refuses_every_caller_but_scribe(client, caller: str) -> None:
    response, _, purge = _transcript_delete(client, "scribe/txn_0123abcd", caller)

    assert response.status_code == 403
    purge.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_closes_rows_cleans_external_stores_then_deletes_rows() -> None:
    """PostgreSQL stays the retry ledger until both external stores are clean."""
    steps: list[str] = []

    def _step(name: str, result=None):
        async def _record(*_args, **_kwargs):
            steps.append(name)
            return result

        return _record

    with (
        patch.object(document_purge.pg_store, "close_artifacts", _step("close")),
        patch.object(
            document_purge.pg_store,
            "list_document_history",
            _step("history", (["a-2", "a-1"], [("kb", "doc")])),
        ),
        patch.object(
            document_purge.pg_store, "get_episode_ids_for_document_history", _step("episodes", [])
        ),
        patch.object(document_purge.graph_module, "delete_kb_episodes", _step("graph")),
        patch.object(document_purge.qdrant_store, "delete_artifacts", _step("qdrant")),
        patch.object(document_purge.pg_store, "delete_artifacts", _step("delete", 2)),
    ):
        assert await document_purge.purge_documents(MagicMock(), "org-1", ["a-2"]) == (2, 0)

    assert steps == ["close", "history", "episodes", "graph", "qdrant", "delete"]
