"""
Tests for SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B4 and B5.

B4: Cross-cluster aware batched naming (_suggest_cluster_names_batched)
B5: cluster_probability_mean replaces dbcv_score in clustering metrics

Six new tests:
1. test_batched_naming_happy_path
2. test_batched_naming_falls_back_when_parse_fails
3. test_batched_naming_partial_response_falls_back_for_missing
4. test_batched_naming_skipped_for_too_many_clusters
5. test_cluster_probability_mean_is_float_or_none
6. test_cluster_persistence_field_present_in_completion_log
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


def _make_cluster_doc_lists(n_clusters: int = 3, docs_per_cluster: int = 8) -> dict:
    """Return {cluster_id: list[DocumentSummary]} for testing."""
    from knowledge_ingest.proposal_generator import DocumentSummary

    result = {}
    for cid in range(n_clusters):
        result[cid] = [
            DocumentSummary(
                title=f"Cluster {cid} doc {j}",
                content_preview=f"Content for cluster {cid} document {j} " + "word " * 15,
            )
            for j in range(docs_per_cluster)
        ]
    return result


def _batched_llm_response(names: dict[int, str]) -> MagicMock:
    """Build mock httpx response for batched naming call."""
    payload = {"names": [{"cluster_id": cid, "name": name} for cid, name in names.items()]}
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(
        return_value={"choices": [{"message": {"content": json.dumps(payload)}}]}
    )
    return mock_resp


def _make_mock_settings() -> MagicMock:
    m = MagicMock()
    m.portal_internal_token = "test-token"
    m.litellm_url = "http://litellm:4000"
    m.litellm_api_key = "key"
    m.taxonomy_classification_model = "klai-fast"
    m.taxonomy_classification_timeout = 30.0
    m.taxonomy_bootstrap_min_cluster_size_floor = 5
    m.taxonomy_bootstrap_cluster_selection_method = "leaf"
    m.taxonomy_bootstrap_max_clusters = 20
    m.taxonomy_bootstrap_top_n_per_cluster = 8
    return m


def _make_clusterable_embeddings(n_clusters: int = 3, n_per_cluster: int = 20) -> np.ndarray:
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
# B4 — Batched naming
# ---------------------------------------------------------------------------


class TestBatchedNaming:
    """B4: _suggest_cluster_names_batched unit and integration tests."""

    @pytest.mark.asyncio
    async def test_batched_naming_happy_path(self):
        """Mock LLM returns 3 names for 3 clusters → all 3 returned in dict.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B4.
        """
        from knowledge_ingest.proposal_generator import _suggest_cluster_names_batched

        cluster_doc_lists = _make_cluster_doc_lists(n_clusters=3)
        expected_names = {0: "CRM Salesforce", 1: "Bellen & Bereikbaarheid", 2: "Facturatie"}

        mock_resp = _batched_llm_response(expected_names)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)

        mock_settings = _make_mock_settings()

        with (
            patch(
                "knowledge_ingest.proposal_generator.httpx.AsyncClient", return_value=mock_client
            ),
            patch("knowledge_ingest.proposal_generator.settings", mock_settings),
        ):
            result = await _suggest_cluster_names_batched(cluster_doc_lists, "VoIP helpdesk KB")

        assert len(result) == 3, f"Expected 3 names, got {len(result)}: {result}"
        assert result[0] == "CRM Salesforce"
        assert result[1] == "Bellen & Bereikbaarheid"
        assert result[2] == "Facturatie"

    @pytest.mark.asyncio
    async def test_batched_naming_falls_back_when_parse_fails(self):
        """LLM returns invalid JSON → _suggest_cluster_names_batched returns empty dict.

        Caller then invokes per-cluster fallback.
        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B4.
        """
        import structlog.testing

        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        embeddings = _make_clusterable_embeddings(n_clusters=2, n_per_cluster=25)
        doc_summaries = _make_doc_summaries(len(embeddings))
        mock_settings = _make_mock_settings()

        per_cluster_call_count = {"n": 0}
        batched_call_count = {"n": 0}

        async def _multi_call_post(*args, **kwargs):
            # First call is the batched call → return invalid JSON
            # Subsequent calls are per-cluster fallbacks → return valid single names
            if batched_call_count["n"] == 0:
                batched_call_count["n"] += 1
                bad_resp = MagicMock()
                bad_resp.raise_for_status = MagicMock()
                bad_resp.json = MagicMock(
                    return_value={"choices": [{"message": {"content": "not valid json {"}}]}
                )
                return bad_resp
            # Per-cluster fallback
            name = f"Fallback Category {per_cluster_call_count['n']}"
            per_cluster_call_count["n"] += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "choices": [{"message": {"content": json.dumps({"category_name": name})}}]
                }
            )
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_multi_call_post)

        submitted_proposals = []

        async def _capture_submit(kb_slug, org_id, proposal):
            submitted_proposals.append(proposal)

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
                    AsyncMock(return_value="desc"),
                ),
            ):
                result = await generate_bootstrap_proposals_v2(
                    org_id="org1",
                    kb_slug="batched-fallback-kb",
                    document_summaries=doc_summaries,
                    document_embeddings=embeddings,
                    existing_nodes=[],
                    kb_description="",
                )

        # bootstrap_naming_fallback_to_per_cluster must be logged (per-cluster path was used)
        log_events = [e["event"] for e in captured]
        assert "bootstrap_naming_fallback_to_per_cluster" in log_events, (
            "Expected bootstrap_naming_fallback_to_per_cluster log when batched parse fails"
        )
        # Bootstrap must still complete and submit proposals
        assert result.proposals_submitted >= 1, (
            "Expected proposals even after batched naming parse failure (fallback should work)"
        )

    @pytest.mark.asyncio
    async def test_batched_naming_partial_response_falls_back_for_missing(self):
        """LLM returns 2 of 3 cluster names → 2 from batched + 1 from per-cluster fallback.

        Net result: 3 proposals, log indicates fallback for 1 cluster.
        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B4.
        """
        import structlog.testing

        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        # Use 3 well-separated clusters
        embeddings = _make_clusterable_embeddings(n_clusters=3, n_per_cluster=20)
        doc_summaries = _make_doc_summaries(len(embeddings))
        mock_settings = _make_mock_settings()
        # Pin EOM here — the fixture is engineered to produce exactly 3
        # clusters and the assertion below counts proposals. Leaf-mode (the
        # production default since SPEC-TAXONOMY-V2-CONSOLIDATION-003) finds
        # the 3 plus 1 sub-structure cluster on this fixture, breaking the
        # count-equality. The test is about FALLBACK behaviour, not cluster
        # count, so EOM is the right pin here.
        mock_settings.taxonomy_bootstrap_cluster_selection_method = "eom"

        batched_call_done = {"done": False}

        async def _partial_post(*args, **kwargs):
            if not batched_call_done["done"]:
                batched_call_done["done"] = True
                # Return only 2 of 3 cluster names (cluster 2 missing)
                payload = {
                    "names": [
                        {"cluster_id": 0, "name": "VoIP Apparaten"},
                        {"cluster_id": 1, "name": "Facturatie"},
                        # cluster 2 intentionally omitted
                    ]
                }
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json = MagicMock(
                    return_value={"choices": [{"message": {"content": json.dumps(payload)}}]}
                )
                return resp
            # Per-cluster fallback for cluster 2
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(
                return_value={
                    "choices": [
                        {"message": {"content": json.dumps({"category_name": "CRM Integraties"})}}
                    ]
                }
            )
            return resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(side_effect=_partial_post)

        submitted_proposals = []

        async def _capture_submit(kb_slug, org_id, proposal):
            submitted_proposals.append(proposal)

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
                    AsyncMock(return_value="desc"),
                ),
            ):
                result = await generate_bootstrap_proposals_v2(
                    org_id="org1",
                    kb_slug="partial-batched-kb",
                    document_summaries=doc_summaries,
                    document_embeddings=embeddings,
                    existing_nodes=[],
                    kb_description="",
                )

        # Should have 3 proposals total (2 from batched + 1 from fallback)
        assert result.proposals_submitted == 3, (
            f"Expected 3 proposals (2 batched + 1 fallback), got {result.proposals_submitted}"
        )

        # Fallback log must have fired for the 1 missing cluster
        log_events = [e["event"] for e in captured]
        assert "bootstrap_naming_fallback_to_per_cluster" in log_events, (
            "Expected bootstrap_naming_fallback_to_per_cluster log for missing cluster"
        )
        fallback_log = next(
            e for e in captured if e.get("event") == "bootstrap_naming_fallback_to_per_cluster"
        )
        assert fallback_log.get("count") == 1, (
            f"Expected fallback count=1, got {fallback_log.get('count')}"
        )

        # Verify the 2 batched names are present
        submitted_names = {p.suggested_name for p in submitted_proposals}
        assert "VoIP Apparaten" in submitted_names
        assert "Facturatie" in submitted_names
        assert "CRM Integraties" in submitted_names

    @pytest.mark.asyncio
    async def test_batched_naming_skipped_for_too_many_clusters(self):
        """31+ clusters → batched call skipped, all clusters go per-cluster.

        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B4 (token-budget guard: >30 clusters).
        """
        import structlog.testing

        from knowledge_ingest.proposal_generator import _suggest_cluster_names_batched

        # Build 31 clusters (above the threshold of 30)
        cluster_doc_lists = _make_cluster_doc_lists(n_clusters=31)
        mock_settings = _make_mock_settings()

        with structlog.testing.capture_logs() as captured:
            with patch("knowledge_ingest.proposal_generator.settings", mock_settings):
                result = await _suggest_cluster_names_batched(cluster_doc_lists, "test KB")

        # Must return empty dict without making any LLM call
        assert result == {}, f"Expected empty dict when n_clusters > 30, got {result}"

        # Must log that batched naming was skipped
        log_events = [e["event"] for e in captured]
        assert "bootstrap_batched_naming_skipped_too_many_clusters" in log_events, (
            "Expected bootstrap_batched_naming_skipped_too_many_clusters log for 31+ clusters"
        )

        skip_log = next(
            e
            for e in captured
            if e.get("event") == "bootstrap_batched_naming_skipped_too_many_clusters"
        )
        assert skip_log.get("n_clusters") == 31
        assert skip_log.get("threshold") == 30


# ---------------------------------------------------------------------------
# B5 — cluster_probability_mean
# ---------------------------------------------------------------------------


class TestClusterPersistenceMean:
    """B5: cluster_probability_mean metric tests."""

    def test_cluster_probability_mean_is_float_or_none(self):
        """cluster_documents_hdbscan metrics dict has cluster_probability_mean (float or None).

        NOT dbcv_score. NOT silhouette_score.
        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B5.
        """
        from knowledge_ingest.clustering import cluster_documents_hdbscan

        rng = np.random.RandomState(42)
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

        # B5: cluster_probability_mean present, dbcv_score absent
        assert "cluster_probability_mean" in metrics, (
            "cluster_probability_mean must be in metrics (B5)"
        )
        assert "dbcv_score" not in metrics, (
            "dbcv_score must NOT be in metrics (replaced by cluster_probability_mean in B5)"
        )
        assert "silhouette_score" not in metrics, (
            "silhouette_score must NOT be in metrics (removed in B3)"
        )

        val = metrics["cluster_probability_mean"]
        assert val is None or isinstance(val, float), (
            f"cluster_probability_mean must be float or None, got {type(val)}"
        )

    @pytest.mark.asyncio
    async def test_cluster_persistence_field_present_in_completion_log(self):
        """bootstrap_proposals_complete log event contains cluster_probability_mean.

        dbcv_score and silhouette_score must NOT be present.
        Full regression guard for B3 + B5.
        SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B5.
        """
        import structlog.testing

        from knowledge_ingest.proposal_generator import generate_bootstrap_proposals_v2

        mock_settings = _make_mock_settings()
        embeddings = _make_clusterable_embeddings(n_clusters=3, n_per_cluster=20)
        doc_summaries = _make_doc_summaries(len(embeddings))

        call_count = {"n": 0}

        async def _naming_post(*args, **kwargs):
            # Handle both batched and per-cluster calls
            payload = kwargs.get("json", {})
            messages = payload.get("messages", [])
            system_msg = next((m["content"] for m in messages if m["role"] == "system"), "")

            if "DISTINCT" in system_msg:
                # Batched naming call
                n_clusters = 3
                names_payload = {
                    "names": [{"cluster_id": i, "name": f"Topic {i}"} for i in range(n_clusters)]
                }
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json = MagicMock(
                    return_value={"choices": [{"message": {"content": json.dumps(names_payload)}}]}
                )
                return resp
            else:
                # Per-cluster fallback call
                name = f"Category {call_count['n']}"
                call_count["n"] += 1
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json = MagicMock(
                    return_value={
                        "choices": [{"message": {"content": json.dumps({"category_name": name})}}]
                    }
                )
                return resp

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
                    AsyncMock(return_value="description"),
                ),
            ):
                await generate_bootstrap_proposals_v2(
                    org_id="org1",
                    kb_slug="persistence-log-test-kb",
                    document_summaries=doc_summaries,
                    document_embeddings=embeddings,
                    existing_nodes=[],
                    kb_description="",
                )

        complete_events = [e for e in captured if e.get("event") == "bootstrap_proposals_complete"]
        assert len(complete_events) >= 1, "Expected at least one bootstrap_proposals_complete event"

        event = complete_events[0]
        # B5: cluster_probability_mean present
        assert "cluster_probability_mean" in event, (
            "bootstrap_proposals_complete must contain cluster_probability_mean (B5)"
        )
        # Full regression: neither old field must appear
        assert "dbcv_score" not in event, (
            "dbcv_score must NOT appear in bootstrap_proposals_complete (replaced by B5)"
        )
        assert "silhouette_score" not in event, (
            "silhouette_score must NOT appear in bootstrap_proposals_complete (removed in B3)"
        )
        # Type check
        val = event["cluster_probability_mean"]
        assert val is None or isinstance(val, float), (
            f"cluster_probability_mean in log must be float or None, got {type(val)}"
        )
