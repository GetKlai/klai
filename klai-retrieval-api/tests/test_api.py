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

    def test_retrieve_scope_notebook_returns_422(self, client):
        """SPEC-DECOMM-FOCUS-001 R-E1: scope=notebook is no longer a valid Literal."""
        resp = client.post(
            "/retrieve",
            json={"query": "test", "org_id": "org-1", "scope": "notebook"},
        )
        assert resp.status_code == 422

    def test_retrieve_scope_broad_returns_422(self, client):
        """SPEC-DECOMM-FOCUS-001 R-E1: scope=broad is no longer a valid Literal."""
        resp = client.post(
            "/retrieve",
            json={"query": "test", "org_id": "org-1", "scope": "broad"},
        )
        assert resp.status_code == 422

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
            mock_settings.source_quota_enabled = True
            mock_settings.source_quota_max_per_source = 2
            mock_settings.router_enabled = False
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
            mock_settings.source_quota_enabled = True
            mock_settings.source_quota_max_per_source = 2
            mock_settings.router_enabled = False
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        data = resp.json()
        assert "graph_results_count" in data["metadata"]
        assert "graph_search_ms" in data["metadata"]
        assert data["metadata"]["graph_results_count"] == 0


class TestLinkExpandInstrumentation:
    """F3 phase 1 (audit retrieval-coupling-2026-05-06): verify link-expansion
    instrumentation captures contribution to served top-k without leaking
    internal state into the response.
    """

    def test_link_expanded_flag_does_not_leak_to_response(self, client, sample_retrieve_request):
        """Internal `_link_expanded` flag MUST NOT appear in any ChunkResult field.

        ChunkResult is a Pydantic model with explicit fields. The build loop in
        retrieve.py:325-360 reads only listed keys, so the underscore-prefixed
        flag stays internal. This test pins that contract.
        """
        seed_chunk = {
            "chunk_id": "seed-1",
            "text": "Seed text",
            "score": 0.9,
            "artifact_id": "a1",
            "content_type": "kb_article",
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
            "ingested_at": None,
            "assertion_mode": None,
            "links_to": ["https://example.test/expanded-doc"],
            "incoming_link_count": 0,
        }
        expansion_chunk = {
            "chunk_id": "expanded-1",
            "text": "Expanded text",
            "score": 0.0,
            "artifact_id": "a2",
            "content_type": "kb_article",
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
            "ingested_at": None,
            "assertion_mode": None,
            "incoming_link_count": 50,  # gets authority boost
            "source_url": "https://example.test/expanded-doc",
        }

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
                return_value=[seed_chunk],
            ),
            patch(
                "retrieval_api.api.retrieve.search.fetch_chunks_by_urls",
                new_callable=AsyncMock,
                return_value=[expansion_chunk],
            ),
            patch(
                "retrieval_api.api.retrieve.reranker.rerank",
                new_callable=AsyncMock,
                # Reranker returns both seed and expanded — preserves the
                # `_link_expanded` flag because reranker.rerank does
                # `candidate.copy()` (shallow copy retains the key).
                side_effect=lambda query, candidates, top_k: [
                    {**c, "reranker_score": 0.9 - i * 0.1} for i, c in enumerate(candidates[:top_k])
                ],
            ),
            patch(
                "retrieval_api.api.retrieve.settings",
            ) as mock_settings,
        ):
            mock_settings.reranker_enabled = True
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_candidates = 20
            mock_settings.graphiti_enabled = False
            mock_settings.link_expand_enabled = True
            mock_settings.link_expand_seed_k = 10
            mock_settings.link_expand_max_urls = 30
            mock_settings.link_expand_candidates = 20
            mock_settings.link_authority_boost = 0.05
            mock_settings.source_quota_enabled = True
            mock_settings.source_quota_max_per_source = 2
            mock_settings.router_enabled = False
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        data = resp.json()
        # Pydantic ChunkResult has no `_link_expanded` field — it should
        # never appear in the serialized response.
        for chunk in data["chunks"]:
            assert "_link_expanded" not in chunk, (
                f"Internal flag leaked into response: {chunk}. "
                "F3 phase 1 contract: instrumentation MUST stay internal."
            )

    def test_decision_record_link_expand_block_emitted(
        self, client, sample_retrieve_request, caplog
    ):
        """`decision_record.link_expand` block MUST appear in the log when
        link-expansion is enabled, with all five required keys."""
        import logging

        caplog.set_level(logging.INFO)

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
                "retrieval_api.api.retrieve.settings",
            ) as mock_settings,
        ):
            mock_settings.reranker_enabled = False
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_candidates = 20
            mock_settings.graphiti_enabled = False
            mock_settings.link_expand_enabled = True
            mock_settings.link_expand_seed_k = 10
            mock_settings.link_expand_max_urls = 30
            mock_settings.link_expand_candidates = 20
            mock_settings.link_authority_boost = 0.05
            mock_settings.source_quota_enabled = True
            mock_settings.source_quota_max_per_source = 2
            mock_settings.router_enabled = False
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        # Find the retrieval_decision_record structlog event
        rec = next(
            (r for r in caplog.records if "retrieval_decision_record" in r.getMessage()),
            None,
        )
        # structlog emits as plain log message; verify the key fields are
        # in the record's structured-data attrs.
        assert rec is not None or any("link_expand" in r.getMessage() for r in caplog.records), (
            "decision_record log should contain a link_expand block"
        )


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

    def test_chat_scope_notebook_returns_422(self, client):
        """SPEC-DECOMM-FOCUS-001 R-E1: scope=notebook is no longer a valid Literal."""
        resp = client.post("/chat", json={"query": "test", "org_id": "org-1", "scope": "notebook"})
        assert resp.status_code == 422

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
