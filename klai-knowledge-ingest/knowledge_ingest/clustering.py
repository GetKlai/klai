"""
Embedding clustering for taxonomy discovery (SPEC-KB-024).

Key functions:
- run_clustering_for_kb: fetch embeddings from Qdrant, HDBSCAN, compute centroids
- classify_by_centroid: cosine similarity lookup (O(k))
- load_centroids / save_centroids: JSON sidecar at ~/.klai/taxonomy_centroids/
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from knowledge_ingest.config import settings

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ClusterEntry:
    cluster_id: int
    centroid: list[float]
    size: int
    taxonomy_node_id: int | None  # None = unconfirmed cluster
    content_label_summary: list[str]


@dataclass
class CentroidStore:
    version: int
    computed_at: str  # ISO 8601
    kb_slug: str
    org_id: str
    clusters: list[ClusterEntry]


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns 0.0 for zero-norm vectors."""
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    denom = norm_a * norm_b
    if denom == 0.0:
        return 0.0
    return dot / denom


# ---------------------------------------------------------------------------
# Centroid-based classification
# ---------------------------------------------------------------------------


def classify_by_centroid(
    embedding: list[float],
    centroids: CentroidStore,
    threshold: float,
    taxonomy_node_ids: set[int],
) -> list[int] | None:
    """Return matched taxonomy node IDs if best centroid sim >= threshold
    AND that centroid maps to a known node. Returns None otherwise (fall through to LLM).
    """
    best_sim = -1.0
    best_cluster: ClusterEntry | None = None

    for cluster in centroids.clusters:
        sim = cosine_similarity(embedding, cluster.centroid)
        if sim > best_sim:
            best_sim = sim
            best_cluster = cluster

    if best_cluster is None or best_sim < threshold:
        return None

    if best_cluster.taxonomy_node_id is None:
        return None

    if best_cluster.taxonomy_node_id not in taxonomy_node_ids:
        return None

    return [best_cluster.taxonomy_node_id]


# ---------------------------------------------------------------------------
# Centroid store I/O
# ---------------------------------------------------------------------------


def load_centroids(org_id: str, kb_slug: str) -> CentroidStore | None:
    """Load centroid store from JSON sidecar. Returns None if not found."""
    path = os.path.expanduser(
        f"{settings.taxonomy_centroids_dir}/{org_id}_{kb_slug}.json"
    )
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("centroid_store_load_failed", path=path)
        return None

    clusters = [ClusterEntry(**c) for c in data.get("clusters", [])]
    return CentroidStore(
        version=data["version"],
        computed_at=data["computed_at"],
        kb_slug=data["kb_slug"],
        org_id=data["org_id"],
        clusters=clusters,
    )


def save_centroids(store: CentroidStore) -> None:
    """Save centroid store to JSON sidecar."""
    base = os.path.expanduser(settings.taxonomy_centroids_dir)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"{store.org_id}_{store.kb_slug}.json")
    data = {
        "version": store.version,
        "computed_at": store.computed_at,
        "kb_slug": store.kb_slug,
        "org_id": store.org_id,
        "clusters": [
            {
                "cluster_id": c.cluster_id,
                "centroid": c.centroid,
                "size": c.size,
                "taxonomy_node_id": c.taxonomy_node_id,
                "content_label_summary": c.content_label_summary,
            }
            for c in store.clusters
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f)
    logger.info("centroid_store_saved", path=path, clusters=len(store.clusters))


# ---------------------------------------------------------------------------
# HDBSCAN clustering
# ---------------------------------------------------------------------------


async def run_clustering_for_kb(
    org_id: str,
    kb_slug: str,
    qdrant_client: AsyncQdrantClient,
    taxonomy_nodes: list,
) -> CentroidStore | None:
    """Fetch embeddings from Qdrant, run HDBSCAN, compute centroids.

    Returns None if KB has < 10 documents.
    Deduplicates to one embedding per document (first chunk per artifact_id).
    """
    import asyncio

    from qdrant_client.models import FieldCondition, Filter, MatchValue

    collection = settings.qdrant_collection

    scroll_filter = Filter(
        must=[
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
            FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
        ]
    )

    # Scroll all chunks, deduplicate to one per artifact_id
    seen_artifacts: set[str] = set()
    doc_embeddings: list[list[float]] = []
    doc_labels: list[list[str]] = []
    doc_artifact_ids: list[str] = []
    offset = None

    while True:
        points, next_offset = await asyncio.wait_for(
            qdrant_client.scroll(
                collection_name=collection,
                scroll_filter=scroll_filter,
                limit=100,
                offset=offset,
                with_payload=["artifact_id", "chunk_index", "content_label"],
                with_vectors=["vector_chunk"],
            ),
            timeout=60.0,
        )

        if not points:
            break

        for point in points:
            payload = point.payload or {}
            artifact_id = payload.get("artifact_id") or str(point.id)
            if artifact_id in seen_artifacts:
                continue
            seen_artifacts.add(artifact_id)

            # Extract vector
            vec = None
            if hasattr(point, "vector") and point.vector:
                if isinstance(point.vector, dict):
                    vec = point.vector.get("vector_chunk")
                elif isinstance(point.vector, list):
                    vec = point.vector

            if vec is None:
                continue

            doc_embeddings.append(vec)
            doc_labels.append(payload.get("content_label") or [])
            doc_artifact_ids.append(artifact_id)

        if next_offset is None:
            break
        offset = next_offset

    if len(doc_embeddings) < 10:
        logger.info(
            "clustering_skipped_too_few_docs",
            org_id=org_id,
            kb_slug=kb_slug,
            doc_count=len(doc_embeddings),
        )
        return None

    # Run HDBSCAN
    import numpy as np

    try:
        from sklearn.cluster import HDBSCAN
    except ImportError:
        logger.error("clustering_sklearn_not_available")
        return None

    X = np.array(doc_embeddings)
    hdb = HDBSCAN(
        min_cluster_size=settings.taxonomy_cluster_min_size,
        metric="cosine",
    )
    labels = hdb.fit_predict(X)

    # Compute centroids per cluster (exclude noise label -1)
    cluster_ids = set(int(lbl) for lbl in labels if lbl >= 0)
    clusters: list[ClusterEntry] = []

    # Load previous store to carry over taxonomy_node_id assignments
    prev_store = load_centroids(org_id, kb_slug)
    prev_map: dict[int, int | None] = {}
    if prev_store:
        for c in prev_store.clusters:
            prev_map[c.cluster_id] = c.taxonomy_node_id

    for cid in sorted(cluster_ids):
        mask = labels == cid
        cluster_vecs = X[mask]
        centroid = cluster_vecs.mean(axis=0).tolist()
        size = int(mask.sum())

        # Collect content labels for docs in this cluster
        label_summary: list[str] = []
        for idx in np.where(mask)[0]:
            label_summary.extend(doc_labels[idx])
        # Deduplicate and take top 5
        seen: set[str] = set()
        unique_labels: list[str] = []
        for lbl in label_summary:
            if lbl not in seen:
                unique_labels.append(lbl)
                seen.add(lbl)
        label_summary = unique_labels[:5]

        # Carry over taxonomy_node_id from previous store if centroid is stable
        taxonomy_node_id: int | None = None
        if prev_store:
            for prev_c in prev_store.clusters:
                if prev_c.taxonomy_node_id is not None:
                    sim = cosine_similarity(centroid, prev_c.centroid)
                    if sim > 0.95:
                        taxonomy_node_id = prev_c.taxonomy_node_id
                        break

        clusters.append(
            ClusterEntry(
                cluster_id=cid,
                centroid=centroid,
                size=size,
                taxonomy_node_id=taxonomy_node_id,
                content_label_summary=label_summary,
            )
        )

    from datetime import UTC, datetime

    store = CentroidStore(
        version=(prev_store.version + 1) if prev_store else 1,
        computed_at=datetime.now(tz=UTC).isoformat(),
        kb_slug=kb_slug,
        org_id=org_id,
        clusters=clusters,
    )

    save_centroids(store)

    logger.info(
        "clustering_complete",
        org_id=org_id,
        kb_slug=kb_slug,
        docs=len(doc_embeddings),
        clusters=len(clusters),
        noise=int((labels == -1).sum()),
    )
    return store
