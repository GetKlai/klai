"""Decision-record logging projection for the /retrieve pipeline.

Extracts the log-safe source-provenance projection from ``retrieve.py``. Pure
(getattr-only, no I/O) and now directly characterization-tested
(tests/test_evidence_pack_decision_sources.py). ``retrieve`` re-imports
``_evidence_pack_decision_sources`` so the orchestrator's decision_record
assembly is unchanged.
"""

from __future__ import annotations


def _evidence_pack_decision_sources(evidence_pack: object) -> list[dict[str, object]]:
    """Return source provenance that is safe and useful in retrieval logs."""
    sources = getattr(evidence_pack, "sources", None)
    if not isinstance(sources, list):
        return []
    decision_sources: list[dict[str, object]] = []
    for source in sources[:5]:
        relevance_score = getattr(source, "relevance_score", None)
        if isinstance(relevance_score, (int, float)):
            relevance_score = round(float(relevance_score), 4)
        decision_sources.append(
            {
                "source_id": getattr(source, "source_id", None),
                "title": getattr(source, "title", None),
                "url": getattr(source, "source_url", None),
                "source_label": getattr(source, "source_label", None),
                "evidence_ids": getattr(source, "evidence_ids", None) or [],
                "relevance_score": relevance_score,
            }
        )
    return decision_sources
