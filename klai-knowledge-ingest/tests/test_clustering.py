"""
Tests for clustering module (SPEC-KB-024).

Covers: cosine_similarity, classify_by_centroid, load/save centroids roundtrip,
and run_clustering_for_kb with too-few docs.
"""

from __future__ import annotations

import tempfile
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical_vectors():
    """Identical vectors should have cosine similarity of 1.0."""
    from knowledge_ingest.clustering import cosine_similarity

    vec = [1.0, 2.0, 3.0]
    assert cosine_similarity(vec, vec) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    """Orthogonal vectors should have cosine similarity of ~0.0."""
    from knowledge_ingest.clustering import cosine_similarity

    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-6)


def test_cosine_similarity_opposite_vectors():
    """Opposite vectors should have cosine similarity of -1.0."""
    from knowledge_ingest.clustering import cosine_similarity

    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector():
    """Zero vector should return 0.0 (no division error)."""
    from knowledge_ingest.clustering import cosine_similarity

    a = [0.0, 0.0]
    b = [1.0, 2.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# classify_by_centroid
# ---------------------------------------------------------------------------


def _make_centroid_store(clusters):
    """Helper to create a CentroidStore for testing."""
    from knowledge_ingest.clustering import CentroidStore

    return CentroidStore(
        version=1,
        computed_at="2026-04-06T00:00:00Z",
        kb_slug="test-kb",
        org_id="org-1",
        clusters=clusters,
    )


def test_classify_by_centroid_above_threshold_known_node():
    """When best centroid sim >= threshold AND maps to a known node, return node_ids."""
    from knowledge_ingest.clustering import ClusterEntry, classify_by_centroid

    clusters = [
        ClusterEntry(
            cluster_id=0,
            centroid=[1.0, 0.0, 0.0],
            size=10,
            taxonomy_node_id=42,
            content_label_summary=["voip"],
        ),
    ]
    store = _make_centroid_store(clusters)

    # Query vector nearly identical to centroid
    result = classify_by_centroid(
        embedding=[0.99, 0.01, 0.0],
        centroids=store,
        threshold=0.85,
        taxonomy_node_ids={42},
    )
    assert result == [42]


def test_classify_by_centroid_above_threshold_unknown_cluster():
    """When best centroid sim >= threshold BUT cluster has no taxonomy_node_id, return None."""
    from knowledge_ingest.clustering import ClusterEntry, classify_by_centroid

    clusters = [
        ClusterEntry(
            cluster_id=0,
            centroid=[1.0, 0.0, 0.0],
            size=10,
            taxonomy_node_id=None,  # unconfirmed cluster
            content_label_summary=["unknown"],
        ),
    ]
    store = _make_centroid_store(clusters)

    result = classify_by_centroid(
        embedding=[0.99, 0.01, 0.0],
        centroids=store,
        threshold=0.85,
        taxonomy_node_ids={42},
    )
    assert result is None


def test_classify_by_centroid_above_threshold_node_not_in_taxonomy():
    """When centroid maps to a node_id that is not in current taxonomy, return None."""
    from knowledge_ingest.clustering import ClusterEntry, classify_by_centroid

    clusters = [
        ClusterEntry(
            cluster_id=0,
            centroid=[1.0, 0.0, 0.0],
            size=10,
            taxonomy_node_id=99,  # not in active taxonomy
            content_label_summary=["old"],
        ),
    ]
    store = _make_centroid_store(clusters)

    result = classify_by_centroid(
        embedding=[0.99, 0.01, 0.0],
        centroids=store,
        threshold=0.85,
        taxonomy_node_ids={42},  # node 99 not here
    )
    assert result is None


def test_classify_by_centroid_below_threshold():
    """When best centroid sim < threshold, return None (fall through to LLM)."""
    from knowledge_ingest.clustering import ClusterEntry, classify_by_centroid

    clusters = [
        ClusterEntry(
            cluster_id=0,
            centroid=[1.0, 0.0, 0.0],
            size=10,
            taxonomy_node_id=42,
            content_label_summary=["voip"],
        ),
    ]
    store = _make_centroid_store(clusters)

    # Orthogonal vector = sim ~0
    result = classify_by_centroid(
        embedding=[0.0, 1.0, 0.0],
        centroids=store,
        threshold=0.85,
        taxonomy_node_ids={42},
    )
    assert result is None


def test_classify_by_centroid_empty_clusters():
    """Empty cluster list returns None."""
    from knowledge_ingest.clustering import classify_by_centroid

    store = _make_centroid_store([])

    result = classify_by_centroid(
        embedding=[1.0, 0.0],
        centroids=store,
        threshold=0.85,
        taxonomy_node_ids={42},
    )
    assert result is None


# ---------------------------------------------------------------------------
# load_centroids / save_centroids roundtrip
# ---------------------------------------------------------------------------


def test_load_centroids_missing_file():
    """Loading centroids for a non-existent file returns None."""
    from knowledge_ingest.clustering import load_centroids

    with patch(
        "knowledge_ingest.clustering.settings",
        type("S", (), {"taxonomy_centroids_dir": tempfile.mkdtemp() + "/nonexistent"})(),
    ):
        result = load_centroids("org-1", "test-kb")
        assert result is None


def test_save_and_load_centroids_roundtrip():
    """Save then load should produce identical data."""
    from knowledge_ingest.clustering import (
        CentroidStore,
        ClusterEntry,
        load_centroids,
        save_centroids,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        mock_settings = type("S", (), {"taxonomy_centroids_dir": tmpdir, "taxonomy_centroid_max_age_hours": 48})()

        store = CentroidStore(
            version=1,
            computed_at="2026-04-06T12:00:00Z",
            kb_slug="voys",
            org_id="org-42",
            clusters=[
                ClusterEntry(
                    cluster_id=0,
                    centroid=[0.1, 0.2, 0.3],
                    size=15,
                    taxonomy_node_id=6,
                    content_label_summary=["voip", "sip"],
                ),
                ClusterEntry(
                    cluster_id=1,
                    centroid=[0.4, 0.5, 0.6],
                    size=8,
                    taxonomy_node_id=None,
                    content_label_summary=["onbekend"],
                ),
            ],
        )

        with patch("knowledge_ingest.clustering.settings", mock_settings):
            save_centroids(store)
            loaded = load_centroids("org-42", "voys")

        assert loaded is not None
        assert loaded.version == store.version
        assert loaded.computed_at == store.computed_at
        assert loaded.kb_slug == store.kb_slug
        assert loaded.org_id == store.org_id
        assert len(loaded.clusters) == 2
        assert loaded.clusters[0].centroid == [0.1, 0.2, 0.3]
        assert loaded.clusters[0].taxonomy_node_id == 6
        assert loaded.clusters[1].taxonomy_node_id is None
        assert loaded.clusters[1].content_label_summary == ["onbekend"]


# ---------------------------------------------------------------------------
# run_clustering_for_kb — too few docs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_clustering_too_few_docs():
    """Should return None when KB has < 10 documents."""
    from unittest.mock import AsyncMock, MagicMock

    from knowledge_ingest.clustering import run_clustering_for_kb

    # Mock Qdrant client returning only 5 points
    mock_client = MagicMock()
    points = []
    for i in range(5):
        p = MagicMock()
        p.payload = {"artifact_id": f"art-{i}", "chunk_index": 0}
        p.vector = {"vector_chunk": [float(i)] * 3}
        points.append(p)

    mock_client.scroll = AsyncMock(return_value=(points, None))

    result = await run_clustering_for_kb(
        org_id="org-1",
        kb_slug="test-kb",
        qdrant_client=mock_client,
        taxonomy_nodes=[],
    )
    assert result is None
