"""Unit tests for SPEC-INFRA-TENANT-DELETE-001 Phase 7.

Covers:
  - graph_module.wipe_org_graph() — unit tests with mocked FalkorDB client
  - POST /internal/v1/orgs/{org_id}/wipe-graph — endpoint tests via TestClient

The endpoint is protected by InternalSecretMiddleware. All successful requests
include the correct X-Internal-Secret header. Auth is covered by the existing
test_middleware_auth.py suite; here we exercise the endpoint logic only.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from knowledge_ingest import graph as graph_module

_ORG_ID = "org-tenant-delete-001"
_SECRET = "test-secret-value-123"
_ENDPOINT = f"/internal/v1/orgs/{_ORG_ID}/wipe-graph"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_falkor_result(deleted_count: int) -> MagicMock:
    result = MagicMock()
    result.result_set = [[deleted_count]]
    return result


def _make_empty_falkor_result() -> MagicMock:
    result = MagicMock()
    result.result_set = []
    return result


# ---------------------------------------------------------------------------
# graph_module.wipe_org_graph() unit tests
# ---------------------------------------------------------------------------


class TestWipeOrgGraph:
    def test_returns_zero_when_graphiti_disabled(self, monkeypatch) -> None:
        monkeypatch.setattr("knowledge_ingest.graph.settings.graphiti_enabled", False)
        result = graph_module.wipe_org_graph(_ORG_ID)
        assert result == 0

    def test_returns_zero_when_falkordb_unavailable(self, monkeypatch) -> None:
        monkeypatch.setattr("knowledge_ingest.graph.settings.graphiti_enabled", True)
        with patch.dict("sys.modules", {"falkordb": None}):
            result = graph_module.wipe_org_graph(_ORG_ID)
        assert result == 0

    def test_returns_node_count_on_success(self, monkeypatch) -> None:
        monkeypatch.setattr("knowledge_ingest.graph.settings.graphiti_enabled", True)
        monkeypatch.setattr("knowledge_ingest.graph.settings.falkordb_host", "localhost")
        monkeypatch.setattr("knowledge_ingest.graph.settings.falkordb_port", 6379)

        mock_graph = MagicMock()
        mock_graph.query.return_value = _make_falkor_result(42)

        mock_client = MagicMock()
        mock_client.select_graph.return_value = mock_graph

        mock_falkordb_cls = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"falkordb": MagicMock(FalkorDB=mock_falkordb_cls)}):
            result = graph_module.wipe_org_graph(_ORG_ID)

        assert result == 42

    def test_sends_correct_cypher_with_org_id_param(self, monkeypatch) -> None:
        monkeypatch.setattr("knowledge_ingest.graph.settings.graphiti_enabled", True)
        monkeypatch.setattr("knowledge_ingest.graph.settings.falkordb_host", "localhost")
        monkeypatch.setattr("knowledge_ingest.graph.settings.falkordb_port", 6379)

        mock_graph = MagicMock()
        mock_graph.query.return_value = _make_falkor_result(7)

        mock_client = MagicMock()
        mock_client.select_graph.return_value = mock_graph

        mock_falkordb_cls = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"falkordb": MagicMock(FalkorDB=mock_falkordb_cls)}):
            graph_module.wipe_org_graph(_ORG_ID)

        mock_client.select_graph.assert_called_once_with(_ORG_ID)
        call_args = mock_graph.query.call_args
        cypher = call_args[0][0]
        assert "MATCH (n)" in cypher
        assert "DETACH DELETE" in cypher
        assert "n.group_id" in cypher
        # params kwarg must carry org_id for tenant isolation
        query_params = call_args.kwargs.get("params", {})
        assert query_params.get("org_id") == _ORG_ID

    def test_returns_zero_when_result_set_empty(self, monkeypatch) -> None:
        monkeypatch.setattr("knowledge_ingest.graph.settings.graphiti_enabled", True)
        monkeypatch.setattr("knowledge_ingest.graph.settings.falkordb_host", "localhost")
        monkeypatch.setattr("knowledge_ingest.graph.settings.falkordb_port", 6379)

        mock_graph = MagicMock()
        mock_graph.query.return_value = _make_empty_falkor_result()

        mock_client = MagicMock()
        mock_client.select_graph.return_value = mock_graph

        mock_falkordb_cls = MagicMock(return_value=mock_client)

        with patch.dict("sys.modules", {"falkordb": MagicMock(FalkorDB=mock_falkordb_cls)}):
            result = graph_module.wipe_org_graph(_ORG_ID)

        assert result == 0


# ---------------------------------------------------------------------------
# POST /internal/v1/orgs/{org_id}/wipe-graph endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def wipe_client():
    """TestClient with InternalSecretMiddleware active, graph_module.wipe_org_graph mocked."""
    from unittest.mock import AsyncMock, patch

    mock_pool = MagicMock()
    mock_pool.close = AsyncMock(return_value=None)

    with (
        patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock),
        patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock),
        patch("knowledge_ingest.config.settings.enrichment_enabled", False),
    ):
        from fastapi.testclient import TestClient

        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


class TestWipeGraphEndpoint:
    def test_200_returns_nodes_deleted_and_status_ok(self, wipe_client) -> None:
        with patch(
            "knowledge_ingest.routes.internal.graph_module.wipe_org_graph",
            return_value=17,
        ):
            resp = wipe_client.post(
                _ENDPOINT,
                headers={"X-Internal-Secret": _SECRET},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["nodes_deleted"] == 17
        assert body["status"] == "ok"

    def test_200_returns_zero_when_graph_empty(self, wipe_client) -> None:
        with patch(
            "knowledge_ingest.routes.internal.graph_module.wipe_org_graph",
            return_value=0,
        ):
            resp = wipe_client.post(
                _ENDPOINT,
                headers={"X-Internal-Secret": _SECRET},
            )
        assert resp.status_code == 200
        assert resp.json()["nodes_deleted"] == 0

    def test_401_without_internal_secret(self, wipe_client) -> None:
        resp = wipe_client.post(_ENDPOINT)
        assert resp.status_code == 401

    def test_401_with_wrong_secret(self, wipe_client) -> None:
        resp = wipe_client.post(
            _ENDPOINT,
            headers={"X-Internal-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401

    def test_org_id_passed_to_wipe_function(self, wipe_client) -> None:
        with patch(
            "knowledge_ingest.routes.internal.graph_module.wipe_org_graph",
            return_value=5,
        ) as mock_wipe:
            wipe_client.post(
                _ENDPOINT,
                headers={"X-Internal-Secret": _SECRET},
            )
        mock_wipe.assert_called_once_with(_ORG_ID)

    def test_idempotent_second_call_returns_zero(self, wipe_client) -> None:
        """Calling twice is safe — second call returns 0 nodes deleted."""
        with patch(
            "knowledge_ingest.routes.internal.graph_module.wipe_org_graph",
            side_effect=[10, 0],
        ):
            resp1 = wipe_client.post(_ENDPOINT, headers={"X-Internal-Secret": _SECRET})
            resp2 = wipe_client.post(_ENDPOINT, headers={"X-Internal-Secret": _SECRET})

        assert resp1.json()["nodes_deleted"] == 10
        assert resp2.json()["nodes_deleted"] == 0
        assert resp1.status_code == 200
        assert resp2.status_code == 200
