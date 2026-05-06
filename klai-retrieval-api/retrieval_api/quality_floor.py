"""Hard quality-score floor filter.

# @MX:NOTE: SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-07 — defence-in-depth.
# @MX:SPEC: SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-07

Defence-in-depth filter for the retrieval pipeline. Removes chunks whose
``quality_score`` falls below a configured threshold (default ``0.05``).

Why this exists
---------------

The ingest-time auth-wall detector (SPEC-INGEST-LOGIN-WALL-DETECT-001 Phase B)
rejects login-walled pages BEFORE they reach Qdrant. But the
``degrade`` mode keeps them in Qdrant with ``quality_score=0.0`` for an
audit-trail. Without a hard floor, ``quality_boost`` would still serve them
because its boost only kicks in after ``feedback_count >= 3`` — meaning a
brand-new degraded chunk has zero negative pull on its retrieval ranking.

This filter is the actual exclusion mechanism for ``quality_score=0.0``
chunks. Sits in the pipeline between source-aware-select and quality_boost
so that:
  1. Source quotas pick from clean candidates (no walled chunk burning a
     diversity slot).
  2. quality_boost only sees passing chunks.

Threshold choice
----------------

Default ``0.05`` is intentional:
  - Default ``quality_score=0.5`` (qdrant_store hard-coded) ALWAYS passes.
  - Only chunks explicitly set to ``0.0`` (``degrade`` mode or a future
    backfill that didn't delete) get filtered.
  - Floating-point drift can't accidentally trigger filtering.

Operators can tune per-deployment via ``KLAI_RETRIEVAL_QUALITY_FLOOR``.
Higher values ARE valid (e.g., 0.6 to surface only quality-boosted chunks)
but require a rationale + alerting plan because they shrink retrieval recall.

Pure: no I/O, no logging, no global state. Sub-millisecond on 1000 chunks.
"""

from __future__ import annotations

# Default fallback when a chunk has no ``quality_score`` field. Mirrors the
# qdrant_store default so legacy chunks (pre-feedback-loop) are never
# filtered just because their payload predates the field.
_DEFAULT_QUALITY = 0.5


def filter_quality_floor(
    chunks: list[dict],
    *,
    floor: float,
) -> tuple[list[dict], int]:
    """Return ``(filtered_chunks, n_filtered)``.

    Removes chunks where ``quality_score < floor`` (strict less-than;
    chunks at exactly ``floor`` PASS). Chunks without a ``quality_score``
    field, or with a non-numeric value, are treated as the default 0.5 and
    pass any floor below 0.5 — a corrupt payload should not crash retrieval.

    Order is preserved. Re-ranking happens elsewhere (rerank /
    quality_boost); this filter only drops.
    """
    if not chunks:
        return [], 0

    kept: list[dict] = []
    n_filtered = 0
    for c in chunks:
        qs = c.get("quality_score", _DEFAULT_QUALITY)
        if not isinstance(qs, (int, float)):
            # Corrupt payload — treat as default rather than crash.
            qs = _DEFAULT_QUALITY
        if qs < floor:
            n_filtered += 1
            continue
        kept.append(c)
    return kept, n_filtered
