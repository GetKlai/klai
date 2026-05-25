"""Tests for API endpoints (/retrieve, /chat, /health)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


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
            # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-07: pre-existing mock gap
            # — fixture didn't set this, so quality_floor compared a real
            # float to a MagicMock and crashed. Locked in by the cleanup PR.
            mock_settings.retrieval_quality_floor = 0.05
            # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1 / REQ-3
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            mock_settings.link_expand_score_boost = 1.00
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
        # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1: confidence_band must
        # be present on every retrieval-path response. reranker_score=0.95
        # ≥ high_threshold=0.60 → 'high'.
        assert data["confidence_band"] == "high", (
            f"reranker_score=0.95 should map to 'high', got {data.get('confidence_band')!r}"
        )


class TestConfidenceBandEndToEnd:
    """SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1: confidence_band emit
    through the full /retrieve pipeline (helper tests in
    test_confidence_band.py cover the pure function in isolation).

    These tests verify the band lands correctly on the response after
    rerank → quality-floor → source-aware-select → quality-boost — i.e.
    that the wiring in retrieve.py picks up the right ``serving`` list.
    """

    @pytest.mark.parametrize(
        ("rerank_score", "expected_band"),
        [
            (0.95, "high"),  # well above 0.60
            (0.45, "medium"),  # between thresholds
            (0.18, "low"),  # the 2026-05-07 Voys-Salesforce incident score
        ],
    )
    def test_band_lands_on_response(
        self, client, sample_retrieve_request, rerank_score, expected_band
    ):
        """End-to-end: synthetic /retrieve with controlled reranker_score
        produces the expected confidence_band on the response.

        Locks in the wiring contract: future refactors of retrieve.py
        that move the band-emit point or change the input list to
        ``_compute_confidence_band`` are caught here.
        """
        chunk_payload = {
            "chunk_id": "c1",
            "text": "Some text",
            "score": 0.9,
            "artifact_id": "a1",
            "content_type": "policy",
            "context_prefix": "Doc: ",
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
            "ingested_at": None,
            "assertion_mode": None,
        }
        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value=sample_retrieve_request["query"],
            ),
            patch(
                "retrieval_api.api.retrieve.embed_single",
                new_callable=AsyncMock,
                return_value=[0.1] * 1024,
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
                return_value=[chunk_payload],
            ),
            patch(
                "retrieval_api.api.retrieve.reranker.rerank",
                new_callable=AsyncMock,
                return_value=[{**chunk_payload, "reranker_score": rerank_score}],
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
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
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            mock_settings.link_expand_score_boost = 1.00
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        data = resp.json()
        assert data["confidence_band"] == expected_band, (
            f"rerank={rerank_score} expected {expected_band}, got {data.get('confidence_band')!r}"
        )

    def test_band_unknown_when_reranker_disabled(self, client, sample_retrieve_request):
        """When reranker is disabled the served list still has chunks, but
        none of them have a meaningful reranker_score — band must be
        ``unknown`` so the litellm-hook falls through to the safety
        injection rather than trusting raw qdrant scores.
        """
        chunk_payload = {
            "chunk_id": "c1",
            "text": "Some text",
            "score": 0.9,
            "artifact_id": "a1",
            "content_type": "policy",
            "context_prefix": "Doc: ",
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
            "ingested_at": None,
            "assertion_mode": None,
        }
        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value=sample_retrieve_request["query"],
            ),
            patch(
                "retrieval_api.api.retrieve.embed_single",
                new_callable=AsyncMock,
                return_value=[0.1] * 1024,
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
                return_value=[chunk_payload],
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
        ):
            mock_settings.reranker_enabled = False  # the difference
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
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            mock_settings.link_expand_score_boost = 1.00
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        assert resp.json()["confidence_band"] == "unknown"

    def test_band_none_on_bypass(self, client, sample_retrieve_request):
        """Gate-bypassed retrieval (smalltalk / out-of-scope query) does
        not run rerank, so band is ``None`` (not ``unknown``). The hook
        treats None as "no signal — leave injection untouched".
        """
        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value=sample_retrieve_request["query"],
            ),
            patch(
                "retrieval_api.api.retrieve.embed_single",
                new_callable=AsyncMock,
                return_value=[0.1] * 1024,
            ),
            patch(
                "retrieval_api.api.retrieve.embed_sparse",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "retrieval_api.api.retrieve.gate.should_bypass",
                new_callable=AsyncMock,
                return_value=(True, 0.5),  # gate bypasses retrieval
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
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
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            mock_settings.link_expand_score_boost = 1.00
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        data = resp.json()
        assert data["retrieval_bypassed"] is True
        assert data["confidence_band"] is None


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
            # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-07: pre-existing mock gap
            # — fixture didn't set this, so quality_floor compared a real
            # float to a MagicMock and crashed. Locked in by the cleanup PR.
            mock_settings.retrieval_quality_floor = 0.05
            # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1 / REQ-3
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            mock_settings.link_expand_score_boost = 1.00
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
            # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-07: pre-existing mock gap
            # — fixture didn't set this, so quality_floor compared a real
            # float to a MagicMock and crashed. Locked in by the cleanup PR.
            mock_settings.retrieval_quality_floor = 0.05
            # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1 / REQ-3
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            mock_settings.link_expand_score_boost = 1.00
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
            # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-07: pre-existing mock gap
            # — fixture didn't set this, so quality_floor compared a real
            # float to a MagicMock and crashed. Locked in by the cleanup PR.
            mock_settings.retrieval_quality_floor = 0.05
            # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1 / REQ-3
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            mock_settings.link_expand_score_boost = 1.00
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200

        # The retrieval_decision_record structlog event MUST be present.
        # Polish 2026-05-06: dropped the previous `or` in the assertion that
        # let the test pass when `rec is None`. structlog routes through
        # stdlib `logging` via ProcessorFormatter, so caplog DOES capture
        # these records — if it doesn't, the F3 instrumentation contract is
        # broken and we want the test to fail loudly.
        record_messages = [r.getMessage() for r in caplog.records]
        decision_records = [m for m in record_messages if "retrieval_decision_record" in m]
        assert decision_records, (
            "retrieval_decision_record log line missing from caplog. "
            f"Captured records: {record_messages}"
        )

        # Verify the link_expand block actually appears with its required keys.
        # The structlog kwargs end up in the record's attribute dict via
        # ProcessorFormatter — search the record's __dict__ for our keys.
        rec = next(r for r in caplog.records if "retrieval_decision_record" in r.getMessage())
        rec_attrs = " ".join(f"{k}={v}" for k, v in rec.__dict__.items())
        for required_key in (
            "link_expand",
            "expanded_in_top_k",
            "seed_in_top_k",
            "served_top_k",
        ):
            assert required_key in rec_attrs, (
                f"link_expand block missing required key '{required_key}'. "
                f"Record attrs: {rec.__dict__}"
            )

    def test_link_expanded_flag_survives_evidence_tier_deep_copy(self):
        """The instrumentation tag MUST survive `copy.deepcopy(reranked)`
        inside ``evidence_tier.apply`` so that when EVIDENCE_SHADOW_MODE=false
        flips on (per SPEC-EVIDENCE-001-FOLLOWUP-001) the deep-copied scored
        chunks STILL carry the flag for ``decision_record.link_expand``.

        This is a contract test on Python semantics — `copy.deepcopy` of a
        dict preserves all keys, and `evidence_tier.apply` mutates in place
        without stripping unknown fields. Pinned here so a future refactor
        of evidence_tier (e.g. switch to a typed constructor that drops
        unknown keys) doesn't silently break F3 instrumentation in the
        post-shadow-mode world.
        """
        import copy

        from retrieval_api.services.evidence_tier import apply

        original = [
            {
                "chunk_id": "expanded-1",
                "text": "expanded chunk",
                "score": 0.5,
                "reranker_score": 0.8,
                "content_type": "kb_article",
                "ingested_at": None,
                "assertion_mode": None,
                "_link_expanded": True,
            },
            {
                "chunk_id": "seed-1",
                "text": "seed chunk",
                "score": 0.9,
                "reranker_score": 0.95,
                "content_type": "kb_article",
                "ingested_at": None,
                "assertion_mode": None,
            },
        ]
        # Simulate the retrieve.py pattern: deepcopy then apply scoring.
        scored = apply(copy.deepcopy(original))

        flagged = [c for c in scored if c.get("_link_expanded") is True]
        assert len(flagged) == 1, (
            f"_link_expanded flag dropped after deepcopy + evidence_tier.apply: "
            f"{[c.get('chunk_id') for c in scored]}. F3 instrumentation broken."
        )
        assert flagged[0]["chunk_id"] == "expanded-1"
        # Original list MUST be untouched (deepcopy guarantee).
        assert "final_score" not in original[0], "deepcopy was lost — apply mutated input"


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

        async def fake_synthesize(query, chunks, history, evidence_pack=None):
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
