"""Tests for API endpoints (/retrieve, /chat, /health)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from retrieval_api.services.evidence_pack import build_evidence_pack


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

    def test_retrieve_happy_path(self, client, sample_retrieve_request, caplog):
        """Happy path: mock all external calls, verify response structure."""
        import logging

        caplog.set_level(logging.INFO)
        observed_step_latencies: dict[str, list[float]] = {}

        def capture_step_metric(*, step: str) -> MagicMock:
            metric = MagicMock()
            metric.observe.side_effect = lambda value: observed_step_latencies.setdefault(
                step, []
            ).append(value)
            return metric

        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value="resolved query",
            ) as mock_coref,
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
                        "title": "Refund policy",
                        "source_url": "https://docs.getklai.com/refunds",
                        "source_label": "klai-help",
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
                        "title": "Refund policy",
                        "source_url": "https://docs.getklai.com/refunds",
                        "source_label": "klai-help",
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
            patch(
                "retrieval_api.api.retrieve.build_evidence_pack",
                wraps=build_evidence_pack,
            ) as mock_evidence_pack,
            patch(
                "retrieval_api.api.retrieve.step_latency_seconds.labels",
                side_effect=capture_step_metric,
            ),
        ):
            # Enable reranker so the rerank mock is actually called
            mock_settings.reranker_enabled = True
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_candidates = 20
            mock_settings.graphiti_enabled = False
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
            # raw_query == query → NOT caller-pre-resolved → retrieval-side
            # coreference runs (this test is the coref-runs canary).
            request = {
                **sample_retrieve_request,
                "raw_query": sample_retrieve_request["query"],
                "kb_slugs": ["support", "sip"],
            }
            resp = client.post("/retrieve", json=request)

        assert resp.status_code == 200
        data = resp.json()
        mock_coref.assert_awaited_once()
        assert data["query_resolved"] == "resolved query"
        assert data["retrieval_bypassed"] is False
        assert len(data["chunks"]) == 1
        assert data["chunks"][0]["chunk_id"] == "c1"
        assert data["chunks"][0]["reranker_score"] == 0.95
        assert mock_evidence_pack.call_args.kwargs["query"] == (
            "What is the refund policy?\nresolved query"
        )
        assert mock_evidence_pack.call_args.kwargs["max_sources"] == 5
        assert data["metadata"]["candidates_retrieved"] == 1
        assert data["metadata"]["retrieval_ms"] > 0
        # SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-1: confidence_band must
        # be present on every retrieval-path response. reranker_score=0.95
        # ≥ high_threshold=0.60 → 'high'.
        assert data["confidence_band"] == "high", (
            f"reranker_score=0.95 should map to 'high', got {data.get('confidence_band')!r}"
        )
        decision_record = next(
            record
            for record in caplog.records
            if "retrieval_decision_record" in record.getMessage()
        )
        decision_attrs = " ".join(
            f"{key}={value}" for key, value in decision_record.__dict__.items()
        )
        assert "https://docs.getklai.com/refunds" in decision_attrs
        assert "Refund policy" in decision_attrs
        assert "top_item_chunk_ids" in decision_attrs
        assert decision_record.msg["confidence_band"] == "high"
        assert "confidence_band_corroborated" not in decision_record.msg
        assert [
            (chunk["chunk_id"], chunk["score"], chunk["reranker_score"]) for chunk in data["chunks"]
        ] == [("c1", 0.9, 0.95)]
        assert {
            key: value
            for key, value in data["metadata"].items()
            if key not in {"retrieval_ms", "rerank_ms"}
        } == {
            "candidates_retrieved": 1,
            "reranked_to": 1,
            "graph_results_count": 0,
            "graph_search_ms": None,
        }
        trace_steps = decision_record.msg["trace_steps"]
        assert [step["name"] for step in trace_steps] == [
            "coreference",
            "embed",
            "gate",
            "router",
            "qdrant_search",
            "graph_search",
            "rerank",
            "quality_floor",
            "source_select",
            "quality_boost",
            "parent_lookup",
            "response_build",
            "confidence_band",
            "total",
        ]
        assert trace_steps[2] == {
            "name": "gate",
            "status": "skipped",
            "duration_ms": 0.0,
            "skipped_reason": "disabled_by_config",
        }
        assert trace_steps[5]["status"] == "skipped"
        assert trace_steps[5]["skipped_reason"] == "disabled_by_config"
        rerank_step = trace_steps[6]
        assert rerank_step["candidates_in"] == 1
        assert rerank_step["candidates_out"] == 1
        steps_by_name = {step["name"]: step for step in trace_steps}
        for trace_name, metric_name in (
            ("coreference", "coref"),
            ("embed", "embed"),
            ("qdrant_search", "qdrant"),
            ("rerank", "rerank"),
            ("total", "total"),
        ):
            assert observed_step_latencies[metric_name] == [
                pytest.approx(steps_by_name[trace_name]["duration_ms"] / 1000, abs=6e-7)
            ]
        assert "coreference_rewrite" not in decision_record.msg

    def test_retrieve_passes_effective_telemetry_level_to_coreference(
        self, client, sample_retrieve_request
    ):
        """Privacy fix (semgrep python-logger-credential-disclosure /
        coreference.py): the endpoint MUST forward the request-scoped
        ``effective_level`` (SPEC-PRIVACY-QUERY-SHADOW-001 min(client,
        canonical) resolution) into ``coreference.resolve`` so the
        destructive-rewrite log gates raw query content on the same level as
        every other telemetry decision in this request."""
        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value="resolved query",
            ) as mock_coref,
            patch(
                "retrieval_api.api.retrieve.get_canonical_level",
                new_callable=AsyncMock,
                return_value="full",
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
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
        ):
            mock_settings.reranker_enabled = False
            mock_settings.retrieval_candidates = 60
            mock_settings.graphiti_enabled = False
            mock_settings.source_quota_enabled = False
            mock_settings.router_enabled = False
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            request = {
                **sample_retrieve_request,
                "raw_query": sample_retrieve_request["query"],
                # Requested upper bound is "full"; canonical is mocked to
                # "full" too, so effective_level resolves to "full".
                "telemetry_level": "full",
            }
            resp = client.post("/retrieve", json=request)

        assert resp.status_code == 200
        mock_coref.assert_awaited_once()
        assert mock_coref.call_args.kwargs["telemetry_level"] == "full"

    @pytest.mark.parametrize(
        "pre_resolved_shape",
        [
            pytest.param("legacy_distinct_raw_query", id="legacy-distinct-raw-query"),
            pytest.param("explicit_flag_guard_fire", id="explicit-flag-guard-fire"),
        ],
    )
    def test_retrieve_skips_coreference_when_caller_pre_resolved(
        self, client, sample_retrieve_request, pre_resolved_shape
    ):
        """Caller pre-resolved → retrieval-api does NOT run a second
        coreference rewrite (kills the double rewrite), responds 200, and uses
        the caller's query verbatim as query_resolved.

        Two shapes signal pre-resolution:
        - legacy: raw_query present and != query (older litellm hook builds);
        - explicit: ``coreference_resolved: true`` — REQUIRED for the
          guard-fire case where the litellm destructive-rewrite guard discards
          the rewrite and sends raw_query == query. Without the flag that
          shape re-triggered retrieval-api's own unguarded rewrite (the
          2026-07-09 "Wat weet je over klai?" → Yealink incident class).

        Regression: the skip branch previously left ``coref_ms`` unbound,
        crashing every pre-resolved request with UnboundLocalError.
        """
        with (
            patch(
                "retrieval_api.api.retrieve.coreference.resolve",
                new_callable=AsyncMock,
                return_value="SHOULD NOT BE USED",
            ) as mock_coref,
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
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "retrieval_api.api.retrieve.reranker.rerank",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
        ):
            mock_settings.reranker_enabled = True
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_candidates = 20
            mock_settings.graphiti_enabled = False
            mock_settings.router_enabled = False
            mock_settings.source_quota_enabled = False
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            if pre_resolved_shape == "legacy_distinct_raw_query":
                request = {
                    **sample_retrieve_request,
                    "raw_query": "totally different pre-rewrite text",
                }
            else:
                request = {
                    **sample_retrieve_request,
                    "raw_query": sample_retrieve_request["query"],
                    "coreference_resolved": True,
                }
            resp = client.post("/retrieve", json=request)

        assert resp.status_code == 200
        mock_coref.assert_not_awaited()
        data = resp.json()
        # query_resolved is the caller's query verbatim — NOT the coref mock value.
        assert data["query_resolved"] == sample_retrieve_request["query"]


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
            mock_settings.source_quota_enabled = True
            mock_settings.source_quota_max_per_source = 2
            mock_settings.router_enabled = False
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
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
            mock_settings.source_quota_enabled = True
            mock_settings.source_quota_max_per_source = 2
            mock_settings.router_enabled = False
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        assert resp.json()["confidence_band"] == "unknown"

    def test_kb_narrow_attempts_retrieval(self, client, sample_retrieve_request):
        """Strict KB mode runs the same mandatory retrieval path."""
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
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_search,
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
        ):
            mock_settings.reranker_enabled = False
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_candidates = 20
            mock_settings.graphiti_enabled = False
            mock_settings.source_quota_enabled = True
            mock_settings.source_quota_max_per_source = 2
            mock_settings.router_enabled = False
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            resp = client.post(
                "/retrieve",
                json={**sample_retrieve_request, "kb_narrow": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["retrieval_bypassed"] is False
        mock_search.assert_awaited_once()


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
            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        data = resp.json()
        assert "graph_results_count" in data["metadata"]
        assert "graph_search_ms" in data["metadata"]
        assert data["metadata"]["graph_results_count"] == 0

    def test_graph_edge_with_provenance_reaches_the_evidence_pack(
        self, client, sample_retrieve_request
    ):
        """SPEC-RAG-GRAPH-CITE-001: a resolved graph fact must be citable.

        Proves the whole chain end to end, because every stage between
        graph_search and the response re-handles the chunk dict: RRF merge,
        reranking, quality boost, source selection and evidence-pack
        assembly. A unit test on _convert_results alone cannot show that
        artifact_id survives all of them.

        Scope note: this test injects a resolved graph chunk, so it locks the
        DOWNSTREAM half of the contract and would also pass on the old code.
        The conversion itself — episode uuid -> artifact_id — is locked by
        test_graph_search.py::test_convert_results_sets_artifact_id_from_episode
        and ::test_graph_edge_with_artifact_id_becomes_citable, which both
        failed before this change.
        """
        graph_chunk = {
            "chunk_id": "graph:edge-1",
            "text": "Nummerbehoud is mogelijk bij overstap",
            "score": 0.9,
            "artifact_id": "artifact-abc",
            "content_type": "graph_edge",
            "context_prefix": None,
            "scope": "org",
            "valid_at": "2026-03-11T00:00:00+00:00",
            "invalid_at": None,
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
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "retrieval_api.api.retrieve.graph_search.search",
                new_callable=AsyncMock,
                return_value=[graph_chunk],
            ),
            patch(
                "retrieval_api.api.retrieve.search.fetch_artifact_display_metadata",
                new_callable=AsyncMock,
                return_value={
                    "artifact-abc": {
                        "title": "Nummerbehoud aanvragen",
                        "source_url": "https://help.example.com/nummerbehoud-aanvragen",
                        "source_label": "help.example.com",
                        "original_filename": None,
                    }
                },
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
        ):
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_enabled = False
            mock_settings.graphiti_enabled = True
            mock_settings.source_quota_enabled = False
            mock_settings.router_enabled = False
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30

            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        pack = resp.json()["evidence_pack"]
        assert pack["no_citable_reason"] is None
        assert [item["chunk_id"] for item in pack["items"]] == ["graph:edge-1"]
        assert pack["items"][0]["artifact_id"] == "artifact-abc"
        assert pack["items"][0]["content_type"] == "graph_edge"
        assert [source["artifact_id"] for source in pack["sources"]] == ["artifact-abc"]
        # The citation names the document, not a truncated fact. Production
        # showed the latter on 2026-08-21: "De paginamap identificeert de
        # Voys-app als een applicatie die kan worden gebruik".
        assert pack["sources"][0]["title"] == "Nummerbehoud aanvragen"
        assert pack["sources"][0]["source_url"] == "https://help.example.com/nummerbehoud-aanvragen"
        # Graphiti's bi-temporal window survives to the served chunk.
        assert resp.json()["chunks"][0]["valid_at"] == "2026-03-11T00:00:00+00:00"

    def test_decision_record_graph_search_tracks_top_k_contribution(
        self, client, sample_retrieve_request, caplog
    ):
        """Issue #71: Graphiti contribution must be measurable before adding traversal."""
        import logging

        caplog.set_level(logging.INFO)
        observed_step_latencies: dict[str, list[float]] = {}

        def capture_step_metric(*, step: str) -> MagicMock:
            metric = MagicMock()
            metric.observe.side_effect = lambda value: observed_step_latencies.setdefault(
                step, []
            ).append(value)
            return metric

        graph_chunk = {
            "chunk_id": "graph:edge-1",
            "text": "Graph edge fact",
            "score": 0.9,
            "artifact_id": None,
            "content_type": "graph_edge",
            "context_prefix": None,
            "scope": "org",
            "valid_at": None,
            "invalid_at": None,
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
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "retrieval_api.api.retrieve.graph_search.search",
                new_callable=AsyncMock,
                return_value=[graph_chunk],
            ),
            patch(
                "retrieval_api.api.retrieve.step_latency_seconds.labels",
                side_effect=capture_step_metric,
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
        ):
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_enabled = False
            mock_settings.graphiti_enabled = True
            mock_settings.source_quota_enabled = False
            mock_settings.router_enabled = False
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30

            resp = client.post("/retrieve", json=sample_retrieve_request)

        assert resp.status_code == 200
        assert resp.json()["metadata"]["graph_results_count"] == 1

        rec = next(r for r in caplog.records if "retrieval_decision_record" in r.getMessage())
        rec_attrs = " ".join(f"{k}={v}" for k, v in rec.__dict__.items())
        for required_key in (
            "graph_search",
            "candidates_returned",
            "graph_in_top_k",
            "graph_top_k_chunk_ids",
        ):
            assert required_key in rec_attrs, (
                f"graph_search block missing required key '{required_key}'. "
                f"Record attrs: {rec.__dict__}"
            )
        assert "graph:edge-1" in rec_attrs
        graph_step = next(step for step in rec.msg["trace_steps"] if step["name"] == "graph_search")
        assert observed_step_latencies["graph"] == [
            pytest.approx(graph_step["duration_ms"] / 1000, abs=6e-7)
        ]


class TestRetrievalTraceEndpoint:
    """Endpoint contracts that cannot be proven by the trace helper alone."""

    def test_graph_search_error_is_safe_and_response_stays_qdrant_based(
        self, client, sample_retrieve_request, caplog
    ):
        import logging

        caplog.set_level(logging.INFO)
        qdrant_chunk = {
            "chunk_id": "qdrant-1",
            "text": "Qdrant result",
            "score": 0.9,
            "artifact_id": "a1",
            "content_type": "policy",
            "context_prefix": None,
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
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[qdrant_chunk],
            ),
            patch(
                "retrieval_api.api.retrieve.graph_search.search",
                new_callable=AsyncMock,
                side_effect=TimeoutError("private query text"),
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
        ):
            mock_settings.ranking_contract_mode = "active"
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_enabled = False
            mock_settings.graphiti_enabled = True
            mock_settings.source_quota_enabled = False
            mock_settings.router_enabled = False
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            response = client.post("/retrieve", json=sample_retrieve_request)

        assert response.status_code == 200
        assert [chunk["chunk_id"] for chunk in response.json()["chunks"]] == ["qdrant-1"]
        record = next(
            item for item in caplog.records if "retrieval_decision_record" in item.getMessage()
        )
        graph_step = next(
            step for step in record.msg["trace_steps"] if step["name"] == "graph_search"
        )
        assert graph_step["status"] == "error"
        assert graph_step["error_type"] == "TimeoutError"
        assert "private query text" not in repr(graph_step)
        assert "traceback" not in repr(graph_step).lower()

    def test_trace_render_failure_does_not_fail_retrieve(
        self, client, sample_retrieve_request, caplog
    ):
        import logging

        caplog.set_level(logging.ERROR)
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
                "retrieval_api.api.retrieve.search.hybrid_search",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "retrieval_api.api.retrieve.RetrievalTrace.to_decision_record",
                side_effect=RuntimeError("render failed"),
            ),
            patch("retrieval_api.api.retrieve.settings") as mock_settings,
        ):
            mock_settings.ranking_contract_mode = "active"
            mock_settings.retrieval_candidates = 60
            mock_settings.reranker_enabled = False
            mock_settings.graphiti_enabled = False
            mock_settings.source_quota_enabled = False
            mock_settings.router_enabled = False
            mock_settings.retrieval_quality_floor = 0.05
            mock_settings.confidence_band_high_threshold = 0.60
            mock_settings.confidence_band_low_threshold = 0.30
            response = client.post("/retrieve", json=sample_retrieve_request)

        assert response.status_code == 200
        assert response.json()["retrieval_bypassed"] is False
        assert any("retrieval_trace_emit_failed" in item.getMessage() for item in caplog.records)


class TestHealthEndpoint:
    def test_health_all_ok(self, client):
        """Health returns 200 when all services are reachable."""
        with (
            patch("httpx.AsyncClient") as MockHttpxClient,
            patch("qdrant_client.AsyncQdrantClient") as MockQdrant,
            # FalkorDB health check runs when graphiti_enabled; an AsyncMock
            # client answers PING without a live falkordb.
            patch("redis.asyncio.Redis", return_value=AsyncMock()),
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
    @patch("retrieval_api.api.chat.search.hybrid_search", new_callable=AsyncMock)
    @patch("retrieval_api.api.chat.reranker.rerank", new_callable=AsyncMock)
    @patch("retrieval_api.api.chat.synthesis.synthesize")
    def test_chat_happy_path(
        self, mock_synth, mock_rerank, mock_search, mock_embed, mock_coref, client
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
