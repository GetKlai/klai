"""Retrieval gate: decide whether to bypass retrieval for trivial queries.

The gate compares the query vector against a set of reference vectors
(pre-embedded at first use) loaded from ``data/gate_reference.jsonl``.
If the margin between the top-1 and top-2 cosine similarities exceeds
``settings.retrieval_gate_threshold``, retrieval is bypassed.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from retrieval_api.config import settings

logger = logging.getLogger(__name__)

_GATE_FILE = Path(__file__).parent.parent / "data" / "gate_reference.jsonl"

# Module-level caches
_reference_queries: list[dict] | None = None
_reference_vectors: list[list[float]] | None = None


def _load_reference_queries() -> list[dict]:
    """Load reference queries from the JSONL file."""
    global _reference_queries
    if _reference_queries is not None:
        return _reference_queries

    if not _GATE_FILE.exists():
        _reference_queries = []
        return _reference_queries

    entries: list[dict] = []
    with open(_GATE_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    _reference_queries = entries
    return _reference_queries


async def _ensure_reference_vectors() -> list[list[float]]:
    """Lazily embed all reference queries on first call, then cache."""
    global _reference_vectors
    if _reference_vectors is not None:
        return _reference_vectors

    queries = _load_reference_queries()
    if not queries:
        _reference_vectors = []
        return _reference_vectors

    try:
        from retrieval_api.services.tei import embed_batch

        texts = [q["query"] for q in queries]
        _reference_vectors = await embed_batch(texts)
    except Exception as exc:
        logger.warning("Failed to embed gate reference queries: %s", exc)
        _reference_vectors = []

    return _reference_vectors


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def should_bypass(query_vector: list[float]) -> tuple[bool, float | None]:
    """Decide whether to bypass retrieval.

    Returns ``(bypass, margin)`` where *bypass* is ``True`` when the gate
    recommends skipping retrieval, and *margin* is the score margin (or
    ``None`` when the gate cannot produce a decision).
    """
    if not settings.retrieval_gate_enabled:
        return False, None

    ref_vectors = await _ensure_reference_vectors()
    if not ref_vectors:
        return False, None

    similarities = [_cosine_similarity(query_vector, rv) for rv in ref_vectors]
    similarities.sort(reverse=True)

    top1 = similarities[0]
    top2 = similarities[1] if len(similarities) > 1 else 0.0
    margin = top1 - top2

    if margin > settings.retrieval_gate_threshold:
        return True, margin
    return False, margin


def reset_cache() -> None:
    """Reset module-level caches (useful for testing)."""
    global _reference_queries, _reference_vectors
    _reference_queries = None
    _reference_vectors = None
