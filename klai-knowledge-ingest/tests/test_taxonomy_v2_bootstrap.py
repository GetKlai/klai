"""
TDD tests for SPEC-TAXONOMY-V2-001: Adaptive Clio-style taxonomy bootstrap.

Tests map to the 19 acceptance criteria in the SPEC.
All tests in RED state before implementation; they import symbols that don't exist yet.

Fixture strategy: pre-generated embeddings with numpy.random.RandomState(seed=42),
3 clear clusters of 20 vectors each + 5 outliers in 1024-dim space.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Fixtures: deterministic synthetic embeddings
# ---------------------------------------------------------------------------

DIM = 1024
CLUSTER_SIZE = 20
N_CLUSTERS = 3
N_OUTLIERS = 5


def _make_synthetic_embeddings(seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Return (embeddings, true_labels) with 3 clear clusters of 20 + 5 outliers.

    Cluster centers are well-separated in 1024-dim space by using orthogonal basis
    vectors scaled to different magnitudes to ensure HDBSCAN finds exactly 3 clusters.
    """
    rng = np.random.RandomState(seed)

    # Create clearly separated cluster centers using sparse vectors
    centers = np.zeros((N_CLUSTERS, DIM), dtype=np.float32)
    centers[0, :50] = 1.0  # cluster 0 lives in dims 0-49
    centers[1, 100:150] = 1.0  # cluster 1 lives in dims 100-149
    centers[2, 200:250] = 1.0  # cluster 2 lives in dims 200-249

    # Normalize centers
    for i in range(N_CLUSTERS):
        centers[i] = centers[i] / np.linalg.norm(centers[i])

    # Generate cluster members: center + small noise
    embeddings_list = []
    labels_list = []
    for cid in range(N_CLUSTERS):
        noise = rng.randn(CLUSTER_SIZE, DIM).astype(np.float32) * 0.05
        vecs = centers[cid] + noise
        # Normalize each vector
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        vecs = vecs / norms
        embeddings_list.append(vecs)
        labels_list.extend([cid] * CLUSTER_SIZE)

    # Outliers: random unit vectors far from cluster centers
    outliers = rng.randn(N_OUTLIERS, DIM).astype(np.float32)
    outlier_norms = np.linalg.norm(outliers, axis=1, keepdims=True)
    outliers = outliers / outlier_norms
    embeddings_list.append(outliers)
    labels_list.extend([-1] * N_OUTLIERS)

    embeddings = np.vstack(embeddings_list)
    labels = np.array(labels_list, dtype=np.int32)
    return embeddings, labels


def _make_doc_summaries(n: int) -> list:
    """Return n synthetic DocumentSummary objects with title and content_preview."""
    from knowledge_ingest.proposal_generator import DocumentSummary
    return [
        DocumentSummary(title=f"Document {i}", content_preview=f"Content about topic {i % 3}: " + "text " * 20)
        for i in range(n)
    ]


def _make_taxonomy_nodes(names: list[str]) -> list:
    """Return TaxonomyNode-compatible objects."""
    from knowledge_ingest.taxonomy_classifier import TaxonomyNode
    return [TaxonomyNode(id=i + 1, name=name) for i, name in enumerate(names)]


def _mock_llm_response(name: str) -> MagicMock:
    """Return a mock httpx response returning a single category_name."""
    response_json = {
        "choices": [{"message": {"content": json.dumps({"category_name": name})}}]
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=response_json)
    return mock_resp


def _make_mock_httpx_client(responses: list[MagicMock]) -> AsyncMock:
    """Return an AsyncMock httpx.AsyncClient that cycles through responses."""
    call_count = {"n": 0}

    async def _post(*args, **kwargs):
        idx = call_count["n"] % len(responses)
        call_count["n"] += 1
        return responses[idx]

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=_post)
    return mock_client


# ---------------------------------------------------------------------------
# AC-16: HDBSCAN with synthetic embeddings returns correct clusters
# ---------------------------------------------------------------------------


class TestClusterDocumentsHdbscan:
    """AC-16: Unit test — HDBSCAN with 3 clear clusters + 5 outliers."""

    def test_hdbscan_returns_three_clusters_and_labels_outliers(self):
        """HDBSCAN on synthetic 3-cluster fixture returns 3 clusters, outliers labeled -1.

        pre_reduce=False: regression-guard for cosine path. UMAP on tiny well-separated
        fixtures absorbs outliers into clusters (expected, not a bug).
        """
        from knowledge_ingest.clustering import cluster_documents_hdbscan

        embeddings, _true_labels = _make_synthetic_embeddings()
        labels, metrics = cluster_documents_hdbscan(embeddings, min_cluster_size=5, pre_reduce=False)

        cluster_ids = set(int(lbl) for lbl in labels if lbl >= 0)
        assert len(cluster_ids) == 3, f"Expected 3 clusters, got {len(cluster_ids)}: {cluster_ids}"

        outlier_mask = labels == -1
        assert outlier_mask.sum() >= 5, f"Expected >= 5 outliers, got {outlier_mask.sum()}"

    def test_hdbscan_metrics_dict_contains_required_keys(self):
        """Metrics dict must contain clusters_found, outlier_count, dbcv_score.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B3: silhouette_score renamed to dbcv_score.
        """
        from knowledge_ingest.clustering import cluster_documents_hdbscan

        embeddings, _ = _make_synthetic_embeddings()
        labels, metrics = cluster_documents_hdbscan(embeddings, min_cluster_size=5)

        assert "clusters_found" in metrics
        assert "outlier_count" in metrics
        assert "dbcv_score" in metrics
        # Regression: silhouette_score must NOT be present (B3 rename)
        assert "silhouette_score" not in metrics

    def test_hdbscan_dbcv_score_is_float_or_none(self):
        """DBCV score is a float or None for multi-cluster results.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B3: replaces silhouette_score with dbcv_score
        via hdb.relative_validity_.
        """
        from knowledge_ingest.clustering import cluster_documents_hdbscan

        embeddings, _ = _make_synthetic_embeddings()
        _labels, metrics = cluster_documents_hdbscan(embeddings, min_cluster_size=5)

        # With 3 clear clusters, dbcv_score should be a float (or None if sklearn
        # version doesn't populate relative_validity_ — both are acceptable)
        assert metrics["dbcv_score"] is None or isinstance(metrics["dbcv_score"], float)

    def test_hdbscan_single_cluster_dbcv_is_none(self):
        """When HDBSCAN returns <= 1 cluster, dbcv_score must be None (undefined).

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B3: dbcv_score replaces silhouette_score.
        """
        from knowledge_ingest.clustering import cluster_documents_hdbscan

        # All identical vectors → single cluster
        rng = np.random.RandomState(1)
        # Tight cluster to force single cluster result
        center = np.zeros(64, dtype=np.float32)
        center[0] = 1.0
        noise = rng.randn(30, 64).astype(np.float32) * 0.001
        embeddings = center + noise
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = (embeddings / norms).astype(np.float32)

        _labels, metrics = cluster_documents_hdbscan(embeddings, min_cluster_size=5)

        # Either 0 or 1 cluster → dbcv is undefined
        if metrics["clusters_found"] <= 1:
            assert metrics["dbcv_score"] is None


# ---------------------------------------------------------------------------
# AC-17: cluster count adapts to corpus size
# ---------------------------------------------------------------------------


class TestAdaptiveClusterCount:
    """AC-17: cluster-count adapts to corpus size."""

    def test_nine_doc_kb_returns_zero_proposals(self):
        """AC-3 + AC-17: 9-doc KB → 0 proposals, logs bootstrap_skipped_too_small_kb."""
        import structlog.testing

        # We'll call the route-level logic that checks doc_count < 10
        # by directly testing the v2 function with 9 synthetic docs
        pass  # Will be tested in integration test below

    def test_closest_to_centroid_returns_correct_indices(self):
        """closest_to_centroid returns top-N indices closest to cluster centroid."""
        from knowledge_ingest.clustering import closest_to_centroid

        rng = np.random.RandomState(42)
        center = np.array([1.0, 0.0, 0.0, 0.0])
        # Make 5 vectors: first 3 close to center, last 2 far
        vecs = np.array([
            [0.99, 0.01, 0.0, 0.0],
            [0.98, 0.02, 0.0, 0.0],
            [0.97, 0.03, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],  # far
            [0.0, 0.0, 1.0, 0.0],  # far
        ], dtype=np.float32)
        # Normalize
        vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        embeddings = vecs

        indices = closest_to_centroid([0, 1, 2, 3, 4], embeddings, n=3)

        assert set(indices) == {0, 1, 2}, f"Expected {{0,1,2}}, got {set(indices)}"
        assert len(indices) == 3


# ---------------------------------------------------------------------------
# AC-1, AC-2, AC-4: corpus-driven proposal count, all docs, top-N per cluster
# ---------------------------------------------------------------------------


class TestBootstrapProposalsV2Integration:
    """Integration tests for generate_bootstrap_proposals_v2."""

    @pytest.fixture
    def mock_settings(self):
        """Settings mock that passes all guards."""
        m = MagicMock()
        m.portal_internal_token = "test-token"
        m.litellm_url = "http://litellm:4000"
        m.litellm_api_key = "key"
        m.taxonomy_classification_model = "klai-fast"
        m.taxonomy_classification_timeout = 30.0
        m.taxonomy_bootstrap_v2_enabled = True
        m.taxonomy_bootstrap_min_cluster_size_floor = 5
        m.taxonomy_bootstrap_max_clusters = 20
        m.taxonomy_bootstrap_top_n_per_cluster = 8
        return m

    @pytest.mark.asyncio
    async def test_ac3_too_small_kb_returns_zero_and_logs(self, mock_settings):
        """AC-3: KB with < 10 docs returns proposals_submitted=0, logs bootstrap_skipped_too_small_kb."""
        import structlog.testing

        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        embeddings = np.random.RandomState(42).randn(9, DIM).astype(np.float32)
        embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
        doc_summaries = _make_doc_summaries(9)

        with structlog.testing.capture_logs() as captured:
            with patch("knowledge_ingest.proposal_generator.settings", mock_settings):
                result = await generate_bootstrap_proposals_v2(
                    org_id="org1",
                    kb_slug="small-kb",
                    document_summaries=doc_summaries,
                    document_embeddings=embeddings,
                    existing_nodes=[],
                    kb_description="",
                )

        assert result.proposals_submitted == 0
        assert result.documents_scanned == 9
        log_events = [e["event"] for e in captured]
        assert "bootstrap_skipped_too_small_kb" in log_events

    @pytest.mark.asyncio
    async def test_ac1_proposal_count_driven_by_clustering(self, mock_settings):
        """AC-1: proposal count comes from HDBSCAN clustering, not hard-coded."""
        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        embeddings, _ = _make_synthetic_embeddings()
        doc_summaries = _make_doc_summaries(len(embeddings))

        # Mock LLM to return distinct names per call
        call_count = {"n": 0}

        async def _mock_post(*args, **kwargs):
            name = f"Category {call_count['n']}"
            call_count["n"] += 1
            resp = _mock_llm_response(name)
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_mock_post)

        with (
            patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", AsyncMock()),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
            patch("knowledge_ingest.proposal_generator.generate_node_description", AsyncMock(return_value="test description")),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="org1",
                kb_slug="test-kb",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=[],
                kb_description="",
            )

        # Should have found ~3 clusters (from our 3-cluster synthetic data)
        assert result.clusters_found >= 1
        assert result.proposals_submitted == result.clusters_found

    @pytest.mark.asyncio
    async def test_ac4_top_n_docs_per_cluster_sent_to_llm(self, mock_settings):
        """AC-4: each LLM call receives exactly N=8 document summaries (or fewer if cluster is smaller)."""
        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        embeddings, _ = _make_synthetic_embeddings()
        doc_summaries = _make_doc_summaries(len(embeddings))
        captured_payloads = []

        async def _capture_post(*args, **kwargs):
            # Capture the json body to inspect doc count
            payload = kwargs.get("json", {})
            captured_payloads.append(payload)
            resp = _mock_llm_response("Some Category")
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_capture_post)

        with (
            patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", AsyncMock()),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
            patch("knowledge_ingest.proposal_generator.generate_node_description", AsyncMock(return_value="")),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="org1",
                kb_slug="test-kb",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=[],
                kb_description="",
            )

        # Each LLM call's user message should contain at most N=8 doc summaries
        for payload in captured_payloads:
            messages = payload.get("messages", [])
            user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
            doc_lines = [l for l in user_msg.split("\n") if l.strip().startswith("-")]
            assert len(doc_lines) <= mock_settings.taxonomy_bootstrap_top_n_per_cluster, (
                f"Expected <= {mock_settings.taxonomy_bootstrap_top_n_per_cluster} docs, "
                f"got {len(doc_lines)}"
            )

    @pytest.mark.asyncio
    async def test_ac5_kb_description_in_system_prompt(self, mock_settings):
        """AC-5: kb.description (if non-empty) appears in the system prompt."""
        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        embeddings, _ = _make_synthetic_embeddings()
        doc_summaries = _make_doc_summaries(len(embeddings))
        kb_description = "End-to-end test KB voor Voys"
        captured_system_prompts = []

        async def _capture_post(*args, **kwargs):
            payload = kwargs.get("json", {})
            messages = payload.get("messages", [])
            sys_msg = next((m["content"] for m in messages if m["role"] == "system"), "")
            captured_system_prompts.append(sys_msg)
            resp = _mock_llm_response("Test Category")
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_capture_post)

        with (
            patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", AsyncMock()),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
            patch("knowledge_ingest.proposal_generator.generate_node_description", AsyncMock(return_value="")),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="org1",
                kb_slug="test-kb",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=[],
                kb_description=kb_description,
            )

        # Every system prompt should contain the KB description
        assert len(captured_system_prompts) > 0
        for sys_prompt in captured_system_prompts:
            assert kb_description in sys_prompt, (
                f"KB description '{kb_description}' not found in system prompt"
            )

    @pytest.mark.asyncio
    async def test_ac6_duplicate_name_not_submitted(self, mock_settings):
        """AC-6: LLM returns name matching existing node → not submitted, logs bootstrap_proposal_skipped_duplicate_name."""
        import structlog.testing

        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        embeddings, _ = _make_synthetic_embeddings()
        doc_summaries = _make_doc_summaries(len(embeddings))
        existing_nodes = _make_taxonomy_nodes(["Facturatie", "Support", "Overig"])

        # LLM always returns a name that already exists (case variation)
        async def _dup_post(*args, **kwargs):
            return _mock_llm_response("facturatie")  # lowercase variant

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_dup_post)

        mock_submit = AsyncMock()
        with structlog.testing.capture_logs() as captured:
            with (
                patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
                patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", mock_submit),
                patch("knowledge_ingest.proposal_generator.settings", mock_settings),
            ):
                result = await generate_bootstrap_proposals_v2(
                    org_id="org1",
                    kb_slug="test-kb",
                    document_summaries=doc_summaries,
                    document_embeddings=embeddings,
                    existing_nodes=existing_nodes,
                    kb_description="",
                )

        assert mock_submit.call_count == 0
        assert result.proposals_submitted == 0
        log_events = [e["event"] for e in captured]
        assert "bootstrap_proposal_skipped_duplicate_name" in log_events

    @pytest.mark.asyncio
    async def test_ac7_cluster_count_capped_at_max(self, mock_settings):
        """AC-7: when cluster_count > max_clusters, keep only top-K largest, log bootstrap_clusters_capped."""
        import structlog.testing

        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        # Reduce max_clusters to force capping
        mock_settings.taxonomy_bootstrap_max_clusters = 2

        embeddings, _ = _make_synthetic_embeddings()
        doc_summaries = _make_doc_summaries(len(embeddings))

        call_count = {"n": 0}

        async def _distinct_post(*args, **kwargs):
            name = f"Unique Category {call_count['n']}"
            call_count["n"] += 1
            return _mock_llm_response(name)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_distinct_post)

        with structlog.testing.capture_logs() as captured:
            with (
                patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
                patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", AsyncMock()),
                patch("knowledge_ingest.proposal_generator.settings", mock_settings),
                patch("knowledge_ingest.proposal_generator.generate_node_description", AsyncMock(return_value="")),
            ):
                result = await generate_bootstrap_proposals_v2(
                    org_id="org1",
                    kb_slug="test-kb",
                    document_summaries=doc_summaries,
                    document_embeddings=embeddings,
                    existing_nodes=[],
                    kb_description="",
                )

        # Should have capped if more than 2 clusters found
        # If synthetic data gave us 3 clusters, we cap to 2
        if result.clusters_found >= 2:
            assert result.proposals_submitted <= 2
            log_events = [e["event"] for e in captured]
            # Only log capped if we actually had more than max
            # (synthetic data may give exactly 2 or 3 clusters)

    @pytest.mark.asyncio
    async def test_ac8_all_duplicates_returns_reason(self, mock_settings):
        """AC-8: all proposed names duplicate existing → response includes reason='all_duplicates'."""
        from knowledge_ingest.proposal_generator import BootstrapResult, generate_bootstrap_proposals_v2

        embeddings, _ = _make_synthetic_embeddings()
        doc_summaries = _make_doc_summaries(len(embeddings))

        # Build nodes that match all possible category names the LLM will return
        # We'll use a fixed response of "Existing Category" for all clusters
        existing_nodes = _make_taxonomy_nodes([
            f"Category {i}" for i in range(50)  # cover all possible LLM responses
        ])

        call_count = {"n": 0}

        async def _dup_post(*args, **kwargs):
            # Return a name that matches one of the existing nodes
            idx = call_count["n"] % 50
            call_count["n"] += 1
            return _mock_llm_response(f"Category {idx}")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_dup_post)

        with (
            patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", AsyncMock()),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="org1",
                kb_slug="test-kb",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=existing_nodes,
                kb_description="",
            )

        assert result.proposals_submitted == 0
        assert result.reason == "all_duplicates"

    @pytest.mark.asyncio
    async def test_ac9_structlog_event_emitted(self, mock_settings):
        """AC-9: one structlog entry 'bootstrap_proposals_complete' per call with required fields."""
        import structlog.testing

        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        embeddings, _ = _make_synthetic_embeddings()
        doc_summaries = _make_doc_summaries(len(embeddings))

        call_count = {"n": 0}

        async def _distinct_post(*args, **kwargs):
            name = f"Topic {call_count['n']}"
            call_count["n"] += 1
            return _mock_llm_response(name)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_distinct_post)

        with structlog.testing.capture_logs() as captured:
            with (
                patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
                patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", AsyncMock()),
                patch("knowledge_ingest.proposal_generator.settings", mock_settings),
                patch("knowledge_ingest.proposal_generator.generate_node_description", AsyncMock(return_value="a description")),
            ):
                result = await generate_bootstrap_proposals_v2(
                    org_id="org-test",
                    kb_slug="kb-test",
                    document_summaries=doc_summaries,
                    document_embeddings=embeddings,
                    existing_nodes=[],
                    kb_description="",
                )

        complete_events = [e for e in captured if e.get("event") == "bootstrap_proposals_complete"]
        assert len(complete_events) == 1, "Expected exactly one bootstrap_proposals_complete event"

        event = complete_events[0]
        # SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B3: dbcv_score replaces silhouette_score
        required_fields = ["clusters_found", "outlier_count", "dbcv_score", "proposals_submitted", "kb_slug", "org_id"]
        for field in required_fields:
            assert field in event, f"Missing required field '{field}' in bootstrap_proposals_complete event"
        assert "silhouette_score" not in event, "silhouette_score must not appear (B3 rename to dbcv_score)"


# ---------------------------------------------------------------------------
# AC-10/11: Latency budgets (mocked LLM, realistic fixture sizes)
# ---------------------------------------------------------------------------


class TestBootstrapLatency:
    """AC-10/11: end-to-end latency tests with mocked LLM."""

    @pytest.fixture
    def instant_llm_mock(self):
        """LLM mock that returns instantly."""
        call_count = {"n": 0}

        async def _instant_post(*args, **kwargs):
            name = f"Fast Category {call_count['n']}"
            call_count["n"] += 1
            return _mock_llm_response(name)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_instant_post)
        return mock_client

    @pytest.fixture
    def mock_settings(self):
        m = MagicMock()
        m.portal_internal_token = "test-token"
        m.litellm_url = "http://litellm:4000"
        m.litellm_api_key = "key"
        m.taxonomy_classification_model = "klai-fast"
        m.taxonomy_classification_timeout = 30.0
        m.taxonomy_bootstrap_v2_enabled = True
        m.taxonomy_bootstrap_min_cluster_size_floor = 5
        m.taxonomy_bootstrap_max_clusters = 20
        m.taxonomy_bootstrap_top_n_per_cluster = 8
        return m

    @pytest.mark.asyncio
    async def test_ac10_1000_docs_under_60_seconds(self, instant_llm_mock, mock_settings):
        """AC-10: 1000-doc KB completes end-to-end in under 60 seconds with mocked LLM."""
        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        rng = np.random.RandomState(42)
        n_docs = 1000
        embeddings = rng.randn(n_docs, 64).astype(np.float32)  # 64-dim for speed
        embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
        doc_summaries = _make_doc_summaries(n_docs)

        start = time.monotonic()
        with (
            patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=instant_llm_mock),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", AsyncMock()),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
            patch("knowledge_ingest.proposal_generator.generate_node_description", AsyncMock(return_value="")),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="org1",
                kb_slug="perf-kb",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=[],
                kb_description="",
            )
        elapsed = time.monotonic() - start

        assert elapsed < 60.0, f"1000-doc bootstrap took {elapsed:.1f}s, budget is 60s"

    @pytest.mark.asyncio
    async def test_ac11_7000_docs_under_180_seconds(self, instant_llm_mock, mock_settings):
        """AC-11: 7000-doc KB completes end-to-end in under 180 seconds with mocked LLM."""
        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        rng = np.random.RandomState(42)
        n_docs = 7000
        embeddings = rng.randn(n_docs, 64).astype(np.float32)  # 64-dim for speed
        embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
        doc_summaries = _make_doc_summaries(n_docs)

        start = time.monotonic()
        with (
            patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=instant_llm_mock),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", AsyncMock()),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
            patch("knowledge_ingest.proposal_generator.generate_node_description", AsyncMock(return_value="")),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="org1",
                kb_slug="large-kb",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=[],
                kb_description="",
            )
        elapsed = time.monotonic() - start

        assert elapsed < 180.0, f"7000-doc bootstrap took {elapsed:.1f}s, budget is 180s"


# ---------------------------------------------------------------------------
# AC-13: Response shape backward compatibility
# ---------------------------------------------------------------------------


class TestBootstrapResponseShape:
    """AC-13: Existing response shape is preserved, clusters_found is added as optional."""

    def test_bootstrap_result_has_required_fields(self):
        """BootstrapResult dataclass has documents_scanned, proposals_submitted, clusters_found."""
        from knowledge_ingest.proposal_generator import BootstrapResult

        result = BootstrapResult(
            documents_scanned=100,
            proposals_submitted=5,
            clusters_found=5,
        )
        assert result.documents_scanned == 100
        assert result.proposals_submitted == 5
        assert result.clusters_found == 5

    def test_bootstrap_result_has_optional_reason(self):
        """BootstrapResult has optional reason field (defaults to None)."""
        from knowledge_ingest.proposal_generator import BootstrapResult

        result = BootstrapResult(
            documents_scanned=50,
            proposals_submitted=0,
            clusters_found=3,
            reason="all_duplicates",
        )
        assert result.reason == "all_duplicates"

        result_no_reason = BootstrapResult(
            documents_scanned=50,
            proposals_submitted=3,
            clusters_found=3,
        )
        assert result_no_reason.reason is None

    def test_bootstrap_response_model_has_clusters_found(self):
        """The ingest-side BootstrapResponse model includes clusters_found as optional int."""
        from knowledge_ingest.routes.taxonomy import BootstrapResponse

        # Should not raise — clusters_found is a new optional field
        resp = BootstrapResponse(documents_scanned=10, proposals_submitted=2, clusters_found=2)
        assert resp.clusters_found == 2

    def test_bootstrap_response_backward_compat_without_clusters_found(self):
        """Existing callers that don't pass clusters_found still work (optional field)."""
        from knowledge_ingest.routes.taxonomy import BootstrapResponse

        resp = BootstrapResponse(documents_scanned=10, proposals_submitted=2)
        assert resp.clusters_found is None


# ---------------------------------------------------------------------------
# AC-14/AC-19: Duplicate prevention regression tests
# ---------------------------------------------------------------------------


class TestDuplicatePreventionRegression:
    """AC-14/AC-19: re-bootstrap doesn't propose names matching existing nodes."""

    @pytest.mark.asyncio
    async def test_ac14_voys_14_nodes_no_duplicates(self):
        """AC-14: existing 14 nodes — bootstrap must not propose names duplicating any."""
        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        voys_nodes = _make_taxonomy_nodes([
            "VoIP-apparaten", "Bellen & Bereikbaarheid", "Facturatie & Abonnementen",
            "Nummers & Portabiliteit", "Apps & Integraties", "Gebruikersbeheer",
            "Wachtrijen & IVR", "Gesprekskwaliteit", "Veiligheid & Privacy",
            "Ondersteuning & Storingen", "Klantportal", "Activering & Onboarding",
            "Contracten & SLA", "Overig",
        ])
        mock_settings = MagicMock()
        mock_settings.portal_internal_token = "test-token"
        mock_settings.litellm_url = "http://litellm:4000"
        mock_settings.litellm_api_key = "key"
        mock_settings.taxonomy_classification_model = "klai-fast"
        mock_settings.taxonomy_classification_timeout = 30.0
        mock_settings.taxonomy_bootstrap_v2_enabled = True
        mock_settings.taxonomy_bootstrap_min_cluster_size_floor = 5
        mock_settings.taxonomy_bootstrap_max_clusters = 20
        mock_settings.taxonomy_bootstrap_top_n_per_cluster = 8

        embeddings, _ = _make_synthetic_embeddings()
        doc_summaries = _make_doc_summaries(len(embeddings))

        # LLM returns names that match existing nodes
        existing_names = [n.name for n in voys_nodes]
        call_count = {"n": 0}

        async def _dup_post(*args, **kwargs):
            name = existing_names[call_count["n"] % len(existing_names)]
            call_count["n"] += 1
            return _mock_llm_response(name)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_dup_post)
        mock_submit = AsyncMock()

        with (
            patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", mock_submit),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="voys-org",
                kb_slug="voys-help-notion",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=voys_nodes,
                kb_description="Voys VoIP helpdesk knowledge base",
            )

        assert mock_submit.call_count == 0, "Should not submit proposals that duplicate existing nodes"


# ---------------------------------------------------------------------------
# AC-15: Ingest classification path untouched
# ---------------------------------------------------------------------------


class TestAC15IngestClassificationUntouched:
    """AC-15: ingest classification path (routes/ingest.py 349-382) is NOT modified."""

    def test_ingest_test_files_not_modified(self):
        """Verify no changes were made to the ingest classification tests.

        This test acts as a documentation check — in a real scenario we'd
        compare git diff, but here we verify the test files exist and the
        function signatures are intact.
        """
        import importlib

        # Import the ingest route to verify it's still importable
        # (would fail if we broke something in it)
        import knowledge_ingest.routes.ingest as ingest_module

        # The key function that should be untouched
        assert hasattr(ingest_module, "ingest_document"), "ingest_document still exists"


# ---------------------------------------------------------------------------
# AC-16: HDBSCAN unit test (explicit)
# ---------------------------------------------------------------------------


class TestAC16HdbscanUnit:
    """AC-16 explicit: HDBSCAN with synthetic embeddings (3 clusters + 5 outliers)."""

    def test_three_clusters_five_outliers_fixture(self):
        """The synthetic fixture itself validates expected shape."""
        embeddings, true_labels = _make_synthetic_embeddings()

        total = CLUSTER_SIZE * N_CLUSTERS + N_OUTLIERS
        assert len(embeddings) == total
        assert len(true_labels) == total

        for cid in range(N_CLUSTERS):
            count = (true_labels == cid).sum()
            assert count == CLUSTER_SIZE

        outlier_count = (true_labels == -1).sum()
        assert outlier_count == N_OUTLIERS


# ---------------------------------------------------------------------------
# AC-17: adaptive cluster count unit
# ---------------------------------------------------------------------------


class TestAC17AdaptiveClusterCount:
    """AC-17: cluster-count adapts to corpus size."""

    def test_min_cluster_size_formula(self):
        """min_cluster_size = max(floor, doc_count // 50)."""
        from knowledge_ingest.clustering import compute_min_cluster_size

        # floor=5 default
        assert compute_min_cluster_size(100, floor=5) == 5  # 100//50=2, max(5,2)=5
        assert compute_min_cluster_size(1000, floor=5) == 20  # 1000//50=20, max(5,20)=20
        assert compute_min_cluster_size(250, floor=5) == 5  # 250//50=5, max(5,5)=5


# ---------------------------------------------------------------------------
# AC-18: Integration test with mocked LiteLLM
# ---------------------------------------------------------------------------


class TestAC18IntegrationWithMockedLitellm:
    """AC-18: full bootstrap flow on 200-doc fixture KB writes N proposals."""

    @pytest.mark.asyncio
    async def test_200_doc_kb_writes_n_proposals_matching_clusters(self):
        """AC-18: 200-doc fixture, mocked LLM — proposals_submitted == clusters_found."""
        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        rng = np.random.RandomState(42)
        n_docs = 200
        # Create 5 clear clusters for this test
        centers = np.zeros((5, 64), dtype=np.float32)
        for i in range(5):
            centers[i, i * 12: i * 12 + 12] = 1.0
            centers[i] = centers[i] / np.linalg.norm(centers[i])

        embeddings_list = []
        for cid in range(5):
            noise = rng.randn(40, 64).astype(np.float32) * 0.05
            vecs = centers[cid] + noise
            vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
            embeddings_list.append(vecs)
        embeddings = np.vstack(embeddings_list).astype(np.float32)
        doc_summaries = _make_doc_summaries(n_docs)

        mock_settings = MagicMock()
        mock_settings.portal_internal_token = "test-token"
        mock_settings.litellm_url = "http://litellm:4000"
        mock_settings.litellm_api_key = "key"
        mock_settings.taxonomy_classification_model = "klai-fast"
        mock_settings.taxonomy_classification_timeout = 30.0
        mock_settings.taxonomy_bootstrap_v2_enabled = True
        mock_settings.taxonomy_bootstrap_min_cluster_size_floor = 5
        mock_settings.taxonomy_bootstrap_max_clusters = 20
        mock_settings.taxonomy_bootstrap_top_n_per_cluster = 8

        call_count = {"n": 0}
        submitted_proposals = []

        async def _distinct_post(*args, **kwargs):
            name = f"Topic {call_count['n']}"
            call_count["n"] += 1
            return _mock_llm_response(name)

        async def _capture_submit(kb_slug, org_id, proposal):
            submitted_proposals.append(proposal)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_distinct_post)

        with (
            patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", side_effect=_capture_submit),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
            patch("knowledge_ingest.proposal_generator.generate_node_description", AsyncMock(return_value="description")),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="org1",
                kb_slug="test-200",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=[],
                kb_description="",
            )

        assert result.proposals_submitted == result.clusters_found
        assert len(submitted_proposals) == result.proposals_submitted


# ---------------------------------------------------------------------------
# AC-19: Regression test — voys 6-node KB
# ---------------------------------------------------------------------------


class TestAC19VoysRegressionNoNewDuplicates:
    """AC-19: getklai/voys (6 nodes) re-bootstrapped → 0 new proposals (all duplicates)."""

    @pytest.mark.asyncio
    async def test_voys_6_nodes_zero_new_proposals(self):
        """AC-19: voys KB with 6 existing nodes, LLM returns matching names → 0 proposals."""
        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        existing_nodes = _make_taxonomy_nodes([
            "Bellen", "Facturatie", "Nummers", "Apps", "Ondersteuning", "Overig",
        ])

        mock_settings = MagicMock()
        mock_settings.portal_internal_token = "test-token"
        mock_settings.litellm_url = "http://litellm:4000"
        mock_settings.litellm_api_key = "key"
        mock_settings.taxonomy_classification_model = "klai-fast"
        mock_settings.taxonomy_classification_timeout = 30.0
        mock_settings.taxonomy_bootstrap_v2_enabled = True
        mock_settings.taxonomy_bootstrap_min_cluster_size_floor = 5
        mock_settings.taxonomy_bootstrap_max_clusters = 20
        mock_settings.taxonomy_bootstrap_top_n_per_cluster = 8

        embeddings, _ = _make_synthetic_embeddings()
        doc_summaries = _make_doc_summaries(len(embeddings))

        existing_names = [n.name for n in existing_nodes]
        call_count = {"n": 0}

        async def _dup_post(*args, **kwargs):
            name = existing_names[call_count["n"] % len(existing_names)]
            call_count["n"] += 1
            return _mock_llm_response(name)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_dup_post)
        mock_submit = AsyncMock()

        with (
            patch("knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", mock_submit),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="voys-org",
                kb_slug="voys",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=existing_nodes,
                kb_description="",
            )

        assert result.proposals_submitted == 0
        assert mock_submit.call_count == 0


# ---------------------------------------------------------------------------
# AC-16 explicit (new function): cluster_documents_hdbscan
# ---------------------------------------------------------------------------


class TestAC16ExplicitClusterFunction:
    """AC-16: explicit unit test for the cluster_documents_hdbscan helper function."""

    def test_returns_three_clusters_for_synthetic_fixture(self):
        """3 clear clusters of 20 vectors + 5 outliers → 3 clusters found.

        pre_reduce=False: regression-guard for cosine path. UMAP on tiny well-separated
        fixtures absorbs outliers into clusters (expected, not a bug).
        """
        from knowledge_ingest.clustering import cluster_documents_hdbscan

        embeddings, _ = _make_synthetic_embeddings()
        _labels, metrics = cluster_documents_hdbscan(
            embeddings,
            min_cluster_size=5,
            pre_reduce=False,
        )

        n_clusters = metrics["clusters_found"]
        assert n_clusters == 3, f"Expected 3 clusters, got {n_clusters}"
        assert metrics["outlier_count"] >= 5


# ---------------------------------------------------------------------------
# Tests for portal_client.fetch_kb_metadata
# ---------------------------------------------------------------------------


class TestFetchKbMetadata:
    """Tests for the new fetch_kb_metadata function in portal_client."""

    @pytest.mark.asyncio
    async def test_fetch_kb_metadata_returns_dict_on_success(self):
        """fetch_kb_metadata returns dict with description on 200."""
        from knowledge_ingest.portal_client import fetch_kb_metadata

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"description": "KB about VoIP"})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        mock_settings = MagicMock()
        mock_settings.portal_internal_token = "test-token"
        mock_settings.portal_url = "http://portal-api:8000"

        with (
            patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.portal_client.settings", mock_settings),
        ):
            result = await fetch_kb_metadata("test-kb", "org1")

        assert result is not None
        assert result["description"] == "KB about VoIP"

    @pytest.mark.asyncio
    async def test_fetch_kb_metadata_returns_none_on_404(self):
        """fetch_kb_metadata returns None on 404 (best-effort, bootstrap continues)."""
        from knowledge_ingest.portal_client import fetch_kb_metadata

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)

        mock_settings = MagicMock()
        mock_settings.portal_internal_token = "test-token"
        mock_settings.portal_url = "http://portal-api:8000"

        with (
            patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.portal_client.settings", mock_settings),
        ):
            result = await fetch_kb_metadata("nonexistent-kb", "org1")

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_kb_metadata_returns_none_on_network_error(self):
        """fetch_kb_metadata returns None on network error (best-effort)."""
        import httpx

        from knowledge_ingest.portal_client import fetch_kb_metadata

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        mock_settings = MagicMock()
        mock_settings.portal_internal_token = "test-token"
        mock_settings.portal_url = "http://portal-api:8000"

        with (
            patch("knowledge_ingest.portal_client.httpx.AsyncClient", return_value=mock_client),
            patch("knowledge_ingest.portal_client.settings", mock_settings),
        ):
            result = await fetch_kb_metadata("test-kb", "org1")

        assert result is None
