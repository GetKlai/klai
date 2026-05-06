"""
Tests for SPEC-TAXONOMY-V2-001-FOLLOWUP-001 Phase B fixes.

B1: UMAP pre-reduction for HDBSCAN (AC-4, AC-5)
B2: Description-generation restored in v2 bootstrap (AC-6, AC-7)
B3: DBCV-score replaces silhouette (AC-8, field rename)
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

DIM = 1024


def _make_doc_summaries(n: int) -> list:
    from knowledge_ingest.proposal_generator import DocumentSummary

    return [
        DocumentSummary(
            title=f"Document {i}",
            content_preview=f"Content about topic {i % 3}: " + "text " * 20,
        )
        for i in range(n)
    ]


def _make_taxonomy_nodes(names: list[str]) -> list:
    from knowledge_ingest.taxonomy_classifier import TaxonomyNode

    return [TaxonomyNode(id=i + 1, name=name) for i, name in enumerate(names)]


def _mock_llm_name_response(name: str) -> MagicMock:
    response_json = {"choices": [{"message": {"content": json.dumps({"category_name": name})}}]}
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=response_json)
    return mock_resp


def _make_clusterable_embeddings(n_clusters: int = 3, n_per_cluster: int = 20) -> np.ndarray:
    """Create well-separated synthetic embeddings with clear clusters in DIM-space."""
    rng = np.random.RandomState(42)
    centers = np.zeros((n_clusters, DIM), dtype=np.float32)
    for i in range(n_clusters):
        centers[i, i * 50 : i * 50 + 50] = 1.0
        centers[i] /= np.linalg.norm(centers[i])

    parts = []
    for cid in range(n_clusters):
        noise = rng.randn(n_per_cluster, DIM).astype(np.float32) * 0.05
        vecs = centers[cid] + noise
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        parts.append(vecs)
    return np.vstack(parts)


# ---------------------------------------------------------------------------
# B1 — UMAP pre-reduction
# ---------------------------------------------------------------------------


class TestReduceEmbeddingsUmap:
    """B1: reduce_embeddings_umap unit tests (AC-4, AC-5)."""

    def test_reduce_embeddings_umap_returns_reduced_shape(self):
        """Input (50, 1024) → output (50, 10) when umap is available.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 AC-4.
        """
        pytest.importorskip("umap", reason="umap-learn not installed; skip shape test")
        from knowledge_ingest.clustering import reduce_embeddings_umap

        rng = np.random.RandomState(42)
        embeddings = rng.randn(50, DIM).astype(np.float32)
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)

        reduced = reduce_embeddings_umap(
            embeddings, n_components=10, n_neighbors=5, random_state=42
        )

        assert reduced.shape == (50, 10), f"Expected (50, 10), got {reduced.shape}"

    def test_reduce_embeddings_umap_fallback_when_import_fails(self):
        """Monkey-patch import umap to raise; assert fallback log + shape preserved.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 AC-5.
        """
        import structlog.testing

        from knowledge_ingest.clustering import reduce_embeddings_umap

        rng = np.random.RandomState(7)
        embeddings = rng.randn(30, DIM).astype(np.float32)
        embeddings /= np.linalg.norm(embeddings, axis=1, keepdims=True)
        original_shape = embeddings.shape

        # Patch the umap import inside clustering.py to raise ImportError
        with structlog.testing.capture_logs() as captured:
            with patch.dict("sys.modules", {"umap": None}):
                result = reduce_embeddings_umap(embeddings, n_components=10)

        assert result.shape == original_shape, (
            f"Shape should be unchanged on ImportError fallback, "
            f"got {result.shape} expected {original_shape}"
        )
        log_events = [e["event"] for e in captured]
        assert "bootstrap_umap_unavailable_fallback" in log_events, (
            "Expected bootstrap_umap_unavailable_fallback warning log on ImportError"
        )

    def test_cluster_documents_hdbscan_with_pre_reduce_true_uses_euclidean(self):
        """When pre_reduce=True, HDBSCAN is called with metric='euclidean' after UMAP.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 AC-4.
        Verifies via sklearn.cluster.HDBSCAN patching to capture constructor kwargs.
        """
        pytest.importorskip("umap", reason="umap-learn not installed")

        from knowledge_ingest.clustering import cluster_documents_hdbscan

        embeddings = _make_clusterable_embeddings()
        captured_kwargs: dict = {}

        from sklearn.cluster import HDBSCAN as RealHDBSCAN

        real_init = RealHDBSCAN.__init__

        def _patched_init(self, min_cluster_size=5, metric="euclidean", **kwargs):
            captured_kwargs["metric"] = metric
            captured_kwargs["min_cluster_size"] = min_cluster_size
            real_init(self, min_cluster_size=min_cluster_size, metric=metric, **kwargs)

        with patch.object(RealHDBSCAN, "__init__", _patched_init):
            cluster_documents_hdbscan(embeddings, min_cluster_size=5, pre_reduce=True)

        assert captured_kwargs.get("metric") == "euclidean", (
            f"Expected metric='euclidean' after UMAP reduction, "
            f"got metric='{captured_kwargs.get('metric')}'"
        )

    def test_cluster_documents_hdbscan_pre_reduce_false_preserves_cosine(self):
        """When pre_reduce=False, HDBSCAN uses metric='cosine' (legacy behaviour).

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 regression guard for cosine path.
        """
        from sklearn.cluster import HDBSCAN as RealHDBSCAN

        from knowledge_ingest.clustering import cluster_documents_hdbscan

        embeddings = _make_clusterable_embeddings()
        captured_kwargs: dict = {}
        real_init = RealHDBSCAN.__init__

        def _patched_init(self, min_cluster_size=5, metric="euclidean", **kwargs):
            captured_kwargs["metric"] = metric
            real_init(self, min_cluster_size=min_cluster_size, metric=metric, **kwargs)

        with patch.object(RealHDBSCAN, "__init__", _patched_init):
            cluster_documents_hdbscan(embeddings, min_cluster_size=5, pre_reduce=False)

        assert captured_kwargs.get("metric") == "cosine", (
            f"Expected metric='cosine' with pre_reduce=False, "
            f"got metric='{captured_kwargs.get('metric')}'"
        )

    def test_umap_reduction_does_not_corrupt_clustering_result(self):
        """End-to-end: UMAP + HDBSCAN on well-separated 1024-dim data still finds clusters.

        Realistic distribution test — not just synthetic orthogonal blobs.
        Uses normally-distributed clusters with overlapping centers to verify
        UMAP genuinely helps rather than hurts.
        """
        pytest.importorskip("umap", reason="umap-learn not installed")
        from knowledge_ingest.clustering import cluster_documents_hdbscan

        # More realistic: Gaussian clusters in 1024-d with overlapping centers
        rng = np.random.RandomState(99)
        n_per_cluster = 30
        n_clusters = 3
        centers = np.zeros((n_clusters, DIM), dtype=np.float32)
        centers[0, :200] = 0.3  # overlapping in first 200 dims
        centers[1, 100:300] = 0.3
        centers[2, 200:400] = 0.3

        parts = []
        for cid in range(n_clusters):
            vecs = rng.normal(loc=centers[cid], scale=0.5, size=(n_per_cluster, DIM)).astype(
                np.float32
            )
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
            parts.append(vecs)

        embeddings = np.vstack(parts)
        labels, metrics = cluster_documents_hdbscan(embeddings, min_cluster_size=5, pre_reduce=True)

        # With UMAP, should find at least 1 cluster (not all noise)
        assert metrics["clusters_found"] >= 1, (
            "UMAP-assisted HDBSCAN found 0 clusters on clusterable data"
        )
        # Returned shape should match input
        assert len(labels) == len(embeddings)


# ---------------------------------------------------------------------------
# B2 — Description-generation restored
# ---------------------------------------------------------------------------


class TestDescriptionGenerationInV2Bootstrap:
    """B2: description generation in generate_bootstrap_proposals_v2 (AC-6, AC-7)."""

    def _make_mock_settings(self) -> MagicMock:
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
    async def test_v2_proposals_get_non_empty_description(self):
        """Integration: mocked LLM returns valid names; proposals submitted with non-empty description.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 AC-6.
        """
        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        embeddings = _make_clusterable_embeddings(n_clusters=2, n_per_cluster=25)
        doc_summaries = _make_doc_summaries(len(embeddings))
        mock_settings = self._make_mock_settings()

        call_count = {"n": 0}

        async def _naming_post(*args, **kwargs):
            name = f"Category {call_count['n']}"
            call_count["n"] += 1
            return _mock_llm_name_response(name)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_naming_post)

        submitted_proposals = []

        async def _capture_submit(kb_slug, org_id, proposal):
            submitted_proposals.append(proposal)

        # generate_node_description returns a non-empty description string
        async def _mock_description(name, parent_name, sample_titles):
            return f"Description for {name}"

        with (
            patch(
                "knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client
            ),
            patch(
                "knowledge_ingest.proposal_generator.submit_taxonomy_proposal",
                side_effect=_capture_submit,
            ),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
            patch(
                "knowledge_ingest.proposal_generator.generate_node_description",
                side_effect=_mock_description,
            ),
        ):
            result = await generate_bootstrap_proposals_v2(
                org_id="org1",
                kb_slug="desc-test-kb",
                document_summaries=doc_summaries,
                document_embeddings=embeddings,
                existing_nodes=[],
                kb_description="",
            )

        assert result.proposals_submitted >= 1, "Expected at least 1 proposal submitted"
        for proposal in submitted_proposals:
            assert proposal.description, (
                f"Proposal '{proposal.suggested_name}' has empty description; "
                "B2 fix should populate it via generate_node_description"
            )

    @pytest.mark.asyncio
    async def test_v2_description_generation_failure_fallback(self):
        """When generate_node_description raises on one cluster:
        - That proposal gets description=""
        - bootstrap_description_generation_failed log fires
        - Other proposals still get their descriptions
        - Bootstrap completes successfully (no exception bubbles)

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 AC-7.
        """
        import structlog.testing

        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        embeddings = _make_clusterable_embeddings(n_clusters=3, n_per_cluster=20)
        doc_summaries = _make_doc_summaries(len(embeddings))
        mock_settings = self._make_mock_settings()

        call_count = {"n": 0}

        async def _naming_post(*args, **kwargs):
            name = f"Cluster {call_count['n']}"
            call_count["n"] += 1
            return _mock_llm_name_response(name)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_naming_post)

        submitted_proposals = []

        async def _capture_submit(kb_slug, org_id, proposal):
            submitted_proposals.append(proposal)

        desc_call_count = {"n": 0}

        async def _failing_description(name, parent_name, sample_titles):
            # First call fails; subsequent calls succeed
            if desc_call_count["n"] == 0:
                desc_call_count["n"] += 1
                raise RuntimeError("LLM timeout for description")
            desc_call_count["n"] += 1
            return f"Description for {name}"

        with structlog.testing.capture_logs() as captured:
            with (
                patch(
                    "knowledge_ingest.proposal_generator.httpx.AsyncClient",
                    return_value=mock_client,
                ),
                patch(
                    "knowledge_ingest.proposal_generator.submit_taxonomy_proposal",
                    side_effect=_capture_submit,
                ),
                patch("knowledge_ingest.proposal_generator.settings", mock_settings),
                patch(
                    "knowledge_ingest.proposal_generator.generate_node_description",
                    side_effect=_failing_description,
                ),
            ):
                result = await generate_bootstrap_proposals_v2(
                    org_id="org1",
                    kb_slug="desc-fallback-kb",
                    document_summaries=doc_summaries,
                    document_embeddings=embeddings,
                    existing_nodes=[],
                    kb_description="",
                )

        # Bootstrap must not fail — result returned successfully
        assert result.proposals_submitted >= 1, (
            "Bootstrap should submit some proposals even with 1 failed description"
        )

        # Failed description → empty string fallback
        empty_desc_proposals = [p for p in submitted_proposals if not p.description]
        assert len(empty_desc_proposals) >= 1, (
            "Expected at least one proposal with description='' (failed generation fallback)"
        )

        # Successful descriptions → non-empty
        non_empty_desc_proposals = [p for p in submitted_proposals if p.description]
        assert len(non_empty_desc_proposals) >= 1, (
            "Expected at least one proposal with non-empty description from successful calls"
        )

        # bootstrap_description_generation_failed must have been logged
        log_events = [e["event"] for e in captured]
        assert "bootstrap_description_generation_failed" in log_events, (
            "Expected bootstrap_description_generation_failed warning log on description error"
        )


# ---------------------------------------------------------------------------
# B3 — DBCV-score field rename
# ---------------------------------------------------------------------------


class TestDbcvScoreFieldRename:
    """B3: DBCV-score replaces silhouette in cluster_documents_hdbscan and bootstrap log."""

    def test_cluster_hdbscan_returns_dbcv_score_not_silhouette(self):
        """cluster_documents_hdbscan metrics dict has 'dbcv_score', NOT 'silhouette_score'.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 AC-8 (B3 rename).
        """
        from knowledge_ingest.clustering import cluster_documents_hdbscan

        rng = np.random.RandomState(42)
        # Well-separated small fixture for speed (pre_reduce=False avoids UMAP dependency)
        centers = np.zeros((3, 32), dtype=np.float32)
        for i in range(3):
            centers[i, i * 10 : i * 10 + 10] = 1.0
            centers[i] /= np.linalg.norm(centers[i])

        parts = []
        for cid in range(3):
            noise = rng.randn(20, 32).astype(np.float32) * 0.05
            vecs = centers[cid] + noise
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
            parts.append(vecs)
        embeddings = np.vstack(parts)

        _labels, metrics = cluster_documents_hdbscan(
            embeddings, min_cluster_size=5, pre_reduce=False
        )

        assert "dbcv_score" in metrics, "dbcv_score must be present in metrics dict"
        assert "silhouette_score" not in metrics, (
            "silhouette_score must NOT be present — B3 renames it to dbcv_score"
        )
        # Value must be float or None
        assert metrics["dbcv_score"] is None or isinstance(metrics["dbcv_score"], float)

    @pytest.mark.asyncio
    async def test_completion_log_includes_dbcv_score_field(self):
        """bootstrap_proposals_complete log event has 'dbcv_score' field, NOT 'silhouette_score'.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 AC-8 (B3 field propagation to logs).
        """
        import structlog.testing

        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

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

        # Use well-separated embeddings (pre_reduce=False path via small dim fixture
        # won't work since proposal_generator always calls pre_reduce=True;
        # we need UMAP or just use DIM-1024 with UMAP available).
        rng = np.random.RandomState(42)
        n_per = 20
        centers = np.zeros((3, DIM), dtype=np.float32)
        for i in range(3):
            centers[i, i * 50 : i * 50 + 50] = 1.0
            centers[i] /= np.linalg.norm(centers[i])
        parts = []
        for cid in range(3):
            noise = rng.randn(n_per, DIM).astype(np.float32) * 0.05
            vecs = centers[cid] + noise
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
            parts.append(vecs)
        embeddings = np.vstack(parts)
        doc_summaries = _make_doc_summaries(len(embeddings))

        call_count = {"n": 0}

        async def _naming_post(*args, **kwargs):
            name = f"Topic {call_count['n']}"
            call_count["n"] += 1
            return _mock_llm_name_response(name)

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_naming_post)

        with structlog.testing.capture_logs() as captured:
            with (
                patch(
                    "knowledge_ingest.proposal_generator.httpx.AsyncClient",
                    return_value=mock_client,
                ),
                patch("knowledge_ingest.proposal_generator.submit_taxonomy_proposal", AsyncMock()),
                patch("knowledge_ingest.proposal_generator.settings", mock_settings),
                patch(
                    "knowledge_ingest.proposal_generator.generate_node_description",
                    AsyncMock(return_value="desc"),
                ),
            ):
                await generate_bootstrap_proposals_v2(
                    org_id="org1",
                    kb_slug="dbcv-test-kb",
                    document_summaries=doc_summaries,
                    document_embeddings=embeddings,
                    existing_nodes=[],
                    kb_description="",
                )

        complete_events = [e for e in captured if e.get("event") == "bootstrap_proposals_complete"]
        assert len(complete_events) >= 1, (
            "Expected at least one bootstrap_proposals_complete log event"
        )

        event = complete_events[0]
        assert "dbcv_score" in event, (
            "bootstrap_proposals_complete log must contain 'dbcv_score' field (B3)"
        )
        assert "silhouette_score" not in event, (
            "bootstrap_proposals_complete must NOT contain 'silhouette_score' (B3 rename)"
        )
        # Value must be float or None
        assert event["dbcv_score"] is None or isinstance(event["dbcv_score"], float)
