"""Tests for API endpoints (/retrieve, /chat, /health)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch




class TestRetrieveEndpoint:
    def test_retrieve_scope_personal_without_user_id(self, client):
        """scope=personal without user_id returns 400."""
        resp = client.post(
            "/retrieve",
            json={"query": "test", "org_id": "org-1", "scope": "personal"},
        )
        assert resp.status_code == 400
        assert "user_id" in resp.json()["detail"]

    def test_retrieve_scope_both_without_user_id(self, client):
        """scope=both without user_id returns 400."""
        resp = client.post(
            "/retrieve",
            json={"query": "test", "org_id": "org-1", "scope": "both"},
        )
        assert resp.status_code == 400
        assert "user_id" in resp.json()["detail"]

    def test_retrieve_scope_notebook_without_notebook_id(self, client):
        """scope=notebook without notebook_id returns 400."""
        resp = client.post(
            "/retrieve",
            json={"query": "test", "org_id": "org-1", "scope": "notebook"},
        )
        assert resp.status_code == 400
        assert "notebook_id" in resp.json()["detail"]

    def test_retrieve_happy_path(self, client, sample_retrieve_request):
        """Happy path: mock all external calls, verify response structure."""
        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value="resolved query",
            ),
            patch(
                "retrieval_api.api.retrieve.embed_single",
                new_callable=AsyncMock,
                return_value=[0.1, 0.2, 0.3],
            ),
            patch(
                "retrieval_api.api.retrieve.gate.should_bypass",
                new_callable=AsyncMock,
                return_value=(False, 0.05),
            ),
            patch(
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "chunk_id": "c1",
                        "text": "Some policy text",
                        "score": 0.9,
                        "artifact_id": "a1",
                        "content_type": "policy",
                        "context_prefix": "Policy: ",
                        "scope": "org",
                        "valid_at": None,
                        "invalid_at": None,
                    }
                ],
            ),
            patch(
                "retrieval_api.api.retrieve.reranker.rerank",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "chunk_id": "c1",
                        "text": "Some policy text",
                        "score": 0.9,
                        "reranker_score": 0.95,
                        "artifact_id": "a1",
                        "content_type": "policy",
                        "context_prefix": "Policy: ",
                        "scope": "org",
                        "valid_at": None,
                        "invalid_at": None,
                    }
                ],
            ),
        ):
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        data = resp.json()
        assert data["query_resolved"] == "resolved query"
        assert data["retrieval_bypassed"] is False
        assert len(data["chunks"]) == 1
        assert data["chunks"][0]["chunk_id"] == "c1"
        assert data["chunks"][0]["reranker_score"] == 0.95
        assert data["metadata"]["candidates_retrieved"] == 1
        assert data["metadata"]["retrieval_ms"] > 0

    def test_retrieve_notebook_no_rerank(self, client):
        """scope=notebook skips reranking."""
        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value="test query",
            ),
            patch(
                "retrieval_api.api.retrieve.embed_single",
                new_callable=AsyncMock,
                return_value=[0.1, 0.2],
            ),
            patch(
                "retrieval_api.api.retrieve.gate.should_bypass",
                new_callable=AsyncMock,
                return_value=(False, None),
            ),
            patch(
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[
                    {
                        "chunk_id": "c1",
                        "text": "notebook chunk",
                        "score": 0.8,
                        "artifact_id": None,
                        "content_type": None,
                        "context_prefix": None,
                        "scope": "notebook",
                        "valid_at": None,
                        "invalid_at": None,
                    }
                ],
            ),
            patch(
                "retrieval_api.api.retrieve.reranker.rerank",
                new_callable=AsyncMock,
            ) as mock_rerank,
        ):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "test",
                    "org_id": "org-1",
                    "scope": "notebook",
                    "notebook_id": "nb-1",
                },
            )

        assert resp.status_code == 200
        # Reranker should NOT have been called for notebook scope
        mock_rerank.assert_not_called()


class TestHealthEndpoint:
    def test_health_all_ok(self, client):
        """Health returns 200 when all services are reachable."""
        with (
            patch("httpx.AsyncClient") as MockHttpxClient,
            patch("qdrant_client.AsyncQdrantClient") as MockQdrant,
        ):
            # Mock httpx for TEI and LiteLLM health checks
            mock_http = AsyncMock()
            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_http.get.return_value = mock_resp
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockHttpxClient.return_value = mock_http

            # Mock Qdrant client
            mock_qc = AsyncMock()
            mock_qc.get_collections.return_value = []
            MockQdrant.return_value = mock_qc

            resp = client.get("/health")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
