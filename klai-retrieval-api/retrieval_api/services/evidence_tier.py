"""Evidence tier scoring for retrieval chunks (SPEC-EVIDENCE-001).

Computes final_score = reranker_score * content_type_weight * assertion_weight * temporal_decay,
then orders chunks in U-shape for optimal LLM injection (Lost in the Middle mitigation).

Feature flags (environment variables):
- EVIDENCE_CONTENT_TYPE_ENABLED (default: true)
- EVIDENCE_TEMPORAL_DECAY_ENABLED (default: true)
- EVIDENCE_ASSERTION_MODE_ENABLED (default: true, but always 1.00 in v1)
"""

from __future__ import annotations

import math
import os
import time
from typing import TypedDict

# -- Configuration objects (R6) ------------------------------------------------


class ContentTypeWeights(TypedDict):
    kb_article: float
    pdf_document: float
    meeting_transcript: float
    # 1on1_transcript is not a valid Python identifier; accessed via string key
    web_crawl: float
    graph_edge: float
    unknown: float


class EvidenceProfile(TypedDict):
    content_type_weights: dict[str, float]
    assertion_mode_weights: dict[str, float]
    temporal_decay: dict[str, float]


# @MX:NOTE: [AUTO] Evidence weights from SPEC-EVIDENCE-001: kb_article=1.00 (human-validated),
# @MX:NOTE: web_crawl=0.65 (external/noisy), unknown=0.55 (defensive minimum).
# @MX:SPEC: SPEC-EVIDENCE-001 R1, R3. Activation of assertion_mode weights: SPEC-EVIDENCE-002.
DEFAULT_EVIDENCE_PROFILE: EvidenceProfile = {
    "content_type_weights": {
        "kb_article": 1.00,
        "pdf_document": 0.90,
        "meeting_transcript": 0.80,
        "1on1_transcript": 0.80,
        "web_crawl": 0.65,
        "graph_edge": 0.70,
        "unknown": 0.55,
    },
    "assertion_mode_weights": {},  # v1: all 1.00, resolved by _assertion_weight
    "temporal_decay": {
        "lt_30": 1.00,
        "d30_180": 0.95,
        "d180_365": 0.90,
        "gt_365": 0.85,
    },
}


# -- Feature flag helpers ------------------------------------------------------


def _is_enabled(env_var: str, default: bool = True) -> bool:
    """Check if a feature flag is enabled via environment variable."""
    val = os.environ.get(env_var, str(default).lower())
    return val.lower() in ("true", "1", "yes")


# -- Scoring functions (R1, R3, R5) --------------------------------------------


def _content_type_weight(content_type: str | None, profile: EvidenceProfile) -> float:
    """Return weight for content_type from profile. Defaults to 'unknown' weight."""
    if not _is_enabled("EVIDENCE_CONTENT_TYPE_ENABLED"):
        return 1.0
    if content_type is None:
        content_type = "unknown"
    return profile["content_type_weights"].get(
        content_type, profile["content_type_weights"]["unknown"]
    )


# PageRank boost constants.
# Scale brings typical FalkorDB algo.pageRank values (0.003–0.05) into log1p-friendly range.
# Alpha caps the max boost at ~25% for hub entities in the current graph size.
# Consistent with the Hebbian boost pattern in graph_search._convert_results.
_PAGERANK_SCALE = 100.0
_PAGERANK_ALPHA = 0.20


def _pagerank_weight(pagerank_max: float | None) -> float:
    """Return multiplicative boost for entity_pagerank_max.

    Uses log-scaled boost so the factor grows meaningfully but not unboundedly
    as graph density increases. Returns 1.0 (no effect) for chunks without
    graph data or when the feature flag is disabled.

    Typical boost range (production graph, 290 entities):
        pagerank_max 0.003 → ×1.05  (+5%)
        pagerank_max 0.010 → ×1.14  (+14%)
        pagerank_max 0.020 → ×1.22  (+22%)
    """
    if not _is_enabled("EVIDENCE_PAGERANK_ENABLED"):
        return 1.0
    if pagerank_max is None or pagerank_max <= 0.0:
        return 1.0
    return 1.0 + _PAGERANK_ALPHA * math.log1p(pagerank_max * _PAGERANK_SCALE)


# @MX:TODO: [AUTO] assertion_mode scoring is flat 1.00 in v1 (plumbing only).
# @MX:SPEC: SPEC-EVIDENCE-002 — activate differential weights after empirical validation.
def _assertion_weight(assertion_mode: str | None, profile: EvidenceProfile) -> float:
    """Return weight for assertion_mode. v1: always 1.00 regardless of mode."""
    # Plumbing only in v1. Activation deferred to SPEC-EVIDENCE-002.
    return 1.00


def _temporal_decay(ingested_at: int | None, profile: EvidenceProfile) -> float:
    """Return temporal decay factor based on chunk age.

    ingested_at is a Unix timestamp. Returns 1.0 when None or when the
    feature flag is disabled.
    """
    if not _is_enabled("EVIDENCE_TEMPORAL_DECAY_ENABLED"):
        return 1.0
    if ingested_at is None:
        return 1.0

    age_days = (time.time() - ingested_at) / 86400
    decay = profile["temporal_decay"]

    if age_days < 30:
        return decay["lt_30"]
    elif age_days < 180:
        return decay["d30_180"]
    elif age_days < 365:
        return decay["d180_365"]
    else:
        return decay["gt_365"]


# -- apply() + U-shape ordering (R7, R2) --------------------------------------


def apply(
    chunks: list[dict],
    profile: EvidenceProfile = DEFAULT_EVIDENCE_PROFILE,
) -> list[dict]:
    """Score chunks with evidence tier weights and reorder in U-shape.

    For each chunk:
        final_score = reranker_score * content_type_weight * assertion_weight * temporal_decay

    Then applies U-shape ordering for optimal LLM injection.
    """
    if not chunks:
        return []

    for chunk in chunks:
        ct_weight = _content_type_weight(chunk.get("content_type"), profile)
        a_weight = _assertion_weight(chunk.get("assertion_mode"), profile)
        t_decay = _temporal_decay(chunk.get("ingested_at"), profile)
        pr_weight = _pagerank_weight(chunk.get("entity_pagerank_max"))

        base_score = chunk.get("reranker_score") or chunk.get("score", 0.0)
        chunk["final_score"] = base_score * ct_weight * a_weight * t_decay * pr_weight
        chunk["evidence_tier_metadata"] = {
            "content_type_weight": ct_weight,
            "assertion_weight": a_weight,
            "temporal_decay": t_decay,
            "pagerank_weight": pr_weight,
        }

    return _order_for_llm(chunks)


# @MX:NOTE: [AUTO] U-shape ordering mitigates Lost in the Middle degradation.
# @MX:SPEC: Liu et al. 2023 (arXiv:2307.03172) — >30% perf drop when best doc in middle.
def _order_for_llm(chunks: list[dict]) -> list[dict]:
    """U-shape ordering: strongest at position 0, second-strongest at last position.

    Algorithm (Lost in the Middle mitigation):
    - Sort chunks by final_score descending: [s0, s1, s2, s3, s4, s5]
    - Even indices go to front: [s0, s2, s4]
    - Odd indices reversed go to back: [s5, s3, s1]
    - Result: [s0, s2, s4, s5, s3, s1]
    """
    if len(chunks) < 3:
        return chunks

    sorted_chunks = sorted(chunks, key=lambda c: c.get("final_score", 0.0), reverse=True)

    front = [sorted_chunks[i] for i in range(0, len(sorted_chunks), 2)]
    back = [sorted_chunks[i] for i in range(1, len(sorted_chunks), 2)]
    back.reverse()

    return front + back
