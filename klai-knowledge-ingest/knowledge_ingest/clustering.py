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
from typing import TYPE_CHECKING, Any

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
    """Load centroid store from JSON sidecar. Returns None if not found or stale."""
    path = os.path.expanduser(f"{settings.taxonomy_centroids_dir}/{org_id}_{kb_slug}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        logger.warning("centroid_store_load_failed", path=path)
        return None

    # SPEC-KB-026 R6: reject stale centroid files
    computed_at_str = data.get("computed_at", "")
    if computed_at_str and settings.taxonomy_centroid_max_age_hours > 0:
        from datetime import UTC, datetime

        try:
            computed_at = datetime.fromisoformat(computed_at_str)
            if computed_at.tzinfo is None:
                # Naive datetime — assume UTC (all our timestamps are stored as UTC).
                computed_at = computed_at.replace(tzinfo=UTC)
            age_hours = (datetime.now(tz=UTC) - computed_at).total_seconds() / 3600
            if age_hours > settings.taxonomy_centroid_max_age_hours:
                logger.warning(
                    "centroid_store_stale",
                    age_hours=round(age_hours, 1),
                    path=path,
                )
                return None
        except (ValueError, TypeError):
            # Unparseable timestamp — treat as stale rather than using potentially old data.
            logger.warning("centroid_store_invalid_timestamp", path=path)
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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    logger.info("centroid_store_saved", path=path, clusters=len(store.clusters))


# ---------------------------------------------------------------------------
# SPEC-TAXONOMY-V2-001: Bootstrap clustering helpers
# ---------------------------------------------------------------------------


def compute_min_cluster_size(doc_count: int, floor: int = 5) -> int:
    """Compute adaptive min_cluster_size for HDBSCAN.

    Formula: max(floor, doc_count // 50). Adapts to corpus size per SPEC-TAXONOMY-V2-001.
    """
    return max(floor, doc_count // 50)


def reduce_embeddings_umap(
    embeddings: Any,
    n_components: int = 10,
    n_neighbors: int = 15,
    random_state: int = 42,
) -> Any:
    """Reduce high-dimensional embeddings via UMAP before clustering.

    SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B1: curse-of-dimensionality mitigation.
    HDBSCAN density estimation degrades in 1024-dim space; UMAP projects to a
    lower-dimensional manifold where density-based clustering is reliable.

    Args:
        embeddings: (n_docs, dim) float32 array of unit-normalised embeddings.
        n_components: target dimensionality after reduction (default: 10, BERTopic best-practice).
        n_neighbors: UMAP n_neighbors parameter (default: 15, BERTopic best-practice).
        random_state: for reproducibility (default: 42).

    Returns:
        Reduced (n_docs, n_components) array, or the original embeddings unchanged
        when umap-learn is not installed (with a warning log).
    """
    try:
        import umap
    except ImportError:
        logger.warning(
            "bootstrap_umap_unavailable_fallback",
            reason="umap-learn not installed; running HDBSCAN on raw embeddings",
        )
        return embeddings

    n_samples = len(embeddings)
    # n_neighbors must be < n_samples; clamp to avoid umap ValueError on small corpora
    effective_n_neighbors = min(n_neighbors, max(2, n_samples - 1))
    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=effective_n_neighbors,
        metric="cosine",
        random_state=random_state,
    )
    return reducer.fit_transform(embeddings)


def cluster_documents_hdbscan(
    embeddings: Any,
    min_cluster_size: int = 5,
    pre_reduce: bool = True,
) -> tuple[Any, dict]:
    """Run HDBSCAN on document embeddings and return (labels, metrics).

    SPEC-TAXONOMY-V2-001 AC-1, AC-16.
    SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B1: UMAP pre-reduction (pre_reduce=True by default).
    SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B5: cluster_probability_mean replaces dbcv_score.
        sklearn 1.8 HDBSCAN does NOT expose relative_validity_ (confirmed in production via
        hasattr() returning False). Use cluster_persistence_ (always available) instead.

    Args:
        embeddings: (n_docs, dim) float32 array of unit-normalised embeddings.
        min_cluster_size: minimum cluster size for HDBSCAN.
        pre_reduce: when True (default), reduce embeddings via UMAP before HDBSCAN
                    and switch HDBSCAN metric to "euclidean" (UMAP output is not cosine-meaningful).
                    When False, run HDBSCAN directly with metric="cosine" (legacy behaviour).

    Returns:
        labels: (n_docs,) int array; -1 = outlier/noise.
        metrics: dict with keys clusters_found, outlier_count, cluster_probability_mean.
                 cluster_probability_mean is None when 0 clusters found or attribute unavailable.
    """
    import numpy as np

    try:
        from sklearn.cluster import HDBSCAN
    except ImportError:
        logger.error("clustering_sklearn_not_available_v2")
        # Return all-outlier labels as fallback
        n = len(embeddings)
        return np.full(n, -1, dtype=np.int32), {
            "clusters_found": 0,
            "outlier_count": n,
            "cluster_probability_mean": None,
        }

    if pre_reduce:
        # Settings drive UMAP runtime params so env-overrides take effect
        # (FOLLOWUP-001 B1). Defaults match BERTopic best-practice for 1k-10k corpora.
        embeddings = reduce_embeddings_umap(
            embeddings,
            n_components=settings.taxonomy_bootstrap_umap_n_components,
            n_neighbors=settings.taxonomy_bootstrap_umap_n_neighbors,
            random_state=settings.taxonomy_bootstrap_umap_random_state,
        )
        hdbscan_metric = "euclidean"
    else:
        hdbscan_metric = "cosine"

    hdb = HDBSCAN(min_cluster_size=min_cluster_size, metric=hdbscan_metric)
    labels = hdb.fit_predict(embeddings)

    cluster_ids = set(int(lbl) for lbl in labels if lbl >= 0)
    clusters_found = len(cluster_ids)
    outlier_count = int((labels == -1).sum())

    # Mean cluster-membership probability — sklearn HDBSCAN's actually-available
    # quality proxy. Higher = more confident clustering (per-point probability of
    # belonging to its assigned cluster, averaged over non-outlier points).
    #
    # Note: sklearn 1.8's HDBSCAN port does NOT expose either `relative_validity_`
    # (DBCV, B3 attempt) or `cluster_persistence_` (B5 attempt). Both attributes
    # exist only in the standalone `hdbscan` package. We use `probabilities_`
    # which sklearn does populate. SPEC-TAXONOMY-V2-001-FOLLOWUP-001 cleanup.
    cluster_probability_mean: float | None = None
    if clusters_found >= 1 and hasattr(hdb, "probabilities_"):
        try:
            probs = hdb.probabilities_
            mask = labels >= 0
            if probs is not None and mask.sum() > 0:
                cluster_probability_mean = float(probs[mask].mean())
        except Exception:
            cluster_probability_mean = None

    return labels, {
        "clusters_found": clusters_found,
        "outlier_count": outlier_count,
        "cluster_probability_mean": cluster_probability_mean,
    }


def closest_to_centroid(
    cluster_indices: list[int],
    embeddings: Any,
    n: int = 8,
) -> list[int]:
    """Return indices of the N documents closest to the cluster centroid.

    SPEC-TAXONOMY-V2-001 AC-4 — per the SPEC pseudocode.

    Args:
        cluster_indices: list of row indices into embeddings that belong to this cluster.
        embeddings: full (n_docs, dim) embedding matrix.
        n: max number of indices to return.

    Returns:
        List of up to n indices from cluster_indices, sorted by cosine similarity
        to the cluster centroid (highest first).
    """
    import numpy as np

    if not cluster_indices:
        return []

    cluster_vecs = embeddings[cluster_indices]
    centroid = cluster_vecs.mean(axis=0)
    centroid_norm = np.linalg.norm(centroid)
    if centroid_norm == 0.0:
        return cluster_indices[:n]

    vec_norms = np.linalg.norm(cluster_vecs, axis=1)
    # Avoid division by zero for zero-norm vectors
    denom = vec_norms * centroid_norm
    denom = np.where(denom == 0.0, 1e-10, denom)
    sims = (cluster_vecs @ centroid) / denom

    top_n_local = list(np.argsort(-sims)[:n])
    return [cluster_indices[i] for i in top_n_local]


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
