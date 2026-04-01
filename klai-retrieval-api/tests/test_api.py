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
                "retrieval_api.api.retrieve.embed_sparse",
                new_callable=AsyncMock,
                return_value=None,
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
                        "ingested_at": None,
                        "assertion_mode": None,
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
                        "ingested_at": None,
                        "assertion_mode": None,
                    }
                ],
            ),
            patch(
                "retrieval_api.api.retrieve.settings",
            ) as mock_settings,
        ):
            # Enable reranker so the rerank mock is actually called
            mock_settings.reranker_enabled = True
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_candidates = 20
            mock_settings.graphiti_enabled = False
            mock_settings.link_expand_enabled = True
            mock_settings.link_expand_seed_k = 10
            mock_settings.link_expand_max_urls = 30
            mock_settings.link_expand_candidates = 20
            mock_settings.link_authority_boost = 0.05
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
                "retrieval_api.api.retrieve.embed_sparse",
                new_callable=AsyncMock,
                return_value=None,
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
                        "ingested_at": None,
                        "assertion_mode": None,
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


class TestGraphMetadata:
    def test_retrieve_metadata_includes_graph_fields(self, client, sample_retrieve_request):
        """Response metadata includes graph_results_count and graph_search_ms (AC-9)."""
        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value="resolved query",
            ),
            patch(
                "retrieval_api.api.retrieve.embed_single",
                new_callable=AsyncMock,
                return_value=[0.1, 0.2],
            ),
            patch(
                "retrieval_api.api.retrieve.embed_sparse",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "retrieval_api.api.retrieve.gate.should_bypass",
                new_callable=AsyncMock,
                return_value=(False, 0.1),
            ),
            patch(
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "retrieval_api.api.retrieve.graph_search.search",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "retrieval_api.api.retrieve.settings",
            ) as mock_settings,
        ):
            mock_settings.retrieval_candidates = 60
            mock_settings.graphiti_enabled = True
            mock_settings.link_expand_enabled = True
            mock_settings.link_expand_seed_k = 10
            mock_settings.link_expand_max_urls = 30
            mock_settings.link_expand_candidates = 20
            mock_settings.link_authority_boost = 0.05
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        data = resp.json()
        assert "graph_results_count" in data["metadata"]
        assert "graph_search_ms" in data["metadata"]
        assert data["metadata"]["graph_results_count"] == 0

    def test_notebook_scope_skips_graph_search(self, client):
        """scope=notebook does not execute graph search (AC-6)."""
        mock_graph_search = AsyncMock(return_value=[])
        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value="q",
            ),
            patch(
                "retrieval_api.api.retrieve.embed_single",
                new_callable=AsyncMock,
                return_value=[0.1],
            ),
            patch(
                "retrieval_api.api.retrieve.embed_sparse",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "retrieval_api.api.retrieve.gate.should_bypass",
                new_callable=AsyncMock,
                return_value=(False, 0.1),
            ),
            patch(
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "retrieval_api.api.retrieve.graph_search.search",
                mock_graph_search,
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
        ):
            mock_settings.retrieval_candidates = 60
            mock_settings.graphiti_enabled = True
            mock_settings.link_expand_enabled = True
            mock_settings.link_expand_seed_k = 10
            mock_settings.link_expand_max_urls = 30
            mock_settings.link_expand_candidates = 20
            mock_settings.link_authority_boost = 0.05
            client.post(
                "/retrieve",
                json={"query": "test", "org_id": "org-1", "scope": "notebook",
                      "notebook_id": "nb-1"},
            )

        mock_graph_search.assert_not_called()


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


class TestChatEndpoint:
    def test_chat_scope_personal_without_user_id(self, client):
        """scope=personal without user_id returns 400."""
        resp = client.post("/chat", json={"query": "test", "org_id": "org-1", "scope": "personal"})
        assert resp.status_code == 400
        assert "user_id" in resp.json()["detail"]

    def test_chat_scope_notebook_without_notebook_id(self, client):
        """scope=notebook without notebook_id returns 400."""
        resp = client.post("/chat", json={"query": "test", "org_id": "org-1", "scope": "notebook"})
        assert resp.status_code == 400
        assert "notebook_id" in resp.json()["detail"]

    @patch(
        "retrieval_api.api.chat.coreference.resolve",
        new_callable=AsyncMock,
        return_value="resolved query",
    )
    @patch("retrieval_api.api.chat.embed_single", new_callable=AsyncMock, return_value=[0.1, 0.2])
    @patch(
        "retrieval_api.api.chat.gate.should_bypass",
        new_callable=AsyncMock,
        return_value=(True, 0.5),
    )
    def test_chat_bypass_path(self, mock_gate, mock_embed, mock_coref, client):
        """Gate bypass returns done event with retrieval_bypassed=True."""
        import json as _json

        with client.stream(
            "POST",
            "/chat",
            json={
                "query": "hello",
                "org_id": "org-1",
                "scope": "org",
            },
        ) as resp:
            assert resp.status_code == 200
            events = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(_json.loads(line[6:]))

        assert len(events) >= 1
        done = events[-1]
        assert done["type"] == "done"
        assert done["retrieval_bypassed"] is True
        assert done["citations"] == []
        assert done["query_resolved"] == "resolved query"

    @patch(
        "retrieval_api.api.chat.coreference.resolve",
        new_callable=AsyncMock,
        return_value="resolved query",
    )
    @patch("retrieval_api.api.chat.embed_single", new_callable=AsyncMock, return_value=[0.1, 0.2])
    @patch(
        "retrieval_api.api.chat.gate.should_bypass",
        new_callable=AsyncMock,
        return_value=(False, 0.05),
    )
    @patch("retrieval_api.api.chat.search.hybrid_search", new_callable=AsyncMock)
    @patch("retrieval_api.api.chat.reranker.rerank", new_callable=AsyncMock)
    @patch("retrieval_api.api.chat.synthesis.synthesize")
    def test_chat_happy_path(
        self, mock_synth, mock_rerank, mock_search, mock_gate, mock_embed, mock_coref, client
    ):
        """Full pipeline: search, rerank, synthesize -- verify token + done events."""
        import json as _json

        mock_search.return_value = [
            {
                "chunk_id": "c1",
                "text": "policy text",
                "score": 0.85,
                "artifact_id": "a1",
                "content_type": "policy",
                "context_prefix": "P: ",
                "scope": "org",
                "valid_at": None,
                "invalid_at": None,
            },
        ]
        mock_rerank.return_value = [
            {
                "chunk_id": "c1",
                "text": "policy text",
                "score": 0.85,
                "artifact_id": "a1",
                "content_type": "policy",
                "context_prefix": "P: ",
                "scope": "org",
                "valid_at": None,
                "invalid_at": None,
                "reranker_score": 0.92,
            },
        ]

        async def fake_synthesize(query, chunks, history):
            yield "Hello"
            yield " world"
            yield {
                "citations": [
                    {
                        "index": 1,
                        "artifact_id": "a1",
                        "title": "P: policy text",
                        "chunk_ids": ["c1"],
                        "relevance_score": 0.92,
                    }
                ],
                "retrieval_bypassed": False,
                "query_resolved": query,
            }

        mock_synth.return_value = fake_synthesize("resolved query", [], [])

        with client.stream(
            "POST",
            "/chat",
            json={
                "query": "What is the refund policy?",
                "org_id": "org-1",
                "scope": "org",
            },
        ) as resp:
            assert resp.status_code == 200
            events = []
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(_json.loads(line[6:]))

        token_events = [e for e in events if e.get("type") == "token"]
        done_events = [e for e in events if e.get("type") == "done"]
        assert len(token_events) >= 1
        assert len(done_events) == 1
        assert done_events[0]["retrieval_bypassed"] is False
        assert len(done_events[0]["citations"]) >= 1
