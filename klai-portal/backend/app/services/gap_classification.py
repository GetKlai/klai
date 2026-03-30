"""Gap classification logic shared between the re-scorer and the LiteLLM hook.

# @MX:NOTE: [AUTO] Mirror of _classify_gap in deploy/litellm/klai_knowledge.py (SPEC-KB-015).
# @MX:NOTE: Threshold env vars KLAI_GAP_SOFT_THRESHOLD / KLAI_GAP_DENSE_THRESHOLD must match
# @MX:NOTE: in both places. Update both files when thresholds change.
"""

from typing import cast

from app.core.config import settings


def classify_gap(chunks: list[dict]) -> str | None:
    """Classify retrieval result. Returns 'hard', 'soft', or None (not a gap).

    Mirrors _classify_gap in deploy/litellm/klai_knowledge.py.
    Reads thresholds from settings (env vars KLAI_GAP_SOFT_THRESHOLD / KLAI_GAP_DENSE_THRESHOLD).
    """
    if not chunks:
        return "hard"
    reranker_scores = cast(
        list[float],
        [c.get("reranker_score") for c in chunks if c.get("reranker_score") is not None],
    )
    if reranker_scores:
        threshold = settings.klai_gap_soft_threshold
        if all(s < threshold for s in reranker_scores):
            return "soft"
    else:
        dense_scores = [c.get("score", 0.0) for c in chunks]
        if all(s < settings.klai_gap_dense_threshold for s in dense_scores):
            return "soft"
    return None
