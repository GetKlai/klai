"""Human review of a support case's machine analysis (SPEC-RAG-SUPPORT-GAP).

The model's analysis (``PortalSupportCase.analysis``) and a person's verdict on
it are stored apart: analysis is regenerated whenever the evidence or the
analyzer version changes, a review is a durable judgement on one displayed
finding. ``PortalSupportCase.reviews`` is a JSONB map keyed by
``"{analysis_revision}:{finding_index}"`` so a review stays bound to the exact
analysis it judged.

The *analysis revision* is a content hash of ``content_hash`` +
``analysis_version`` + the raw analysis JSON. It changes the moment any of those
move, which is exactly when a stored review no longer describes what the reviewer
saw. Old-revision keys stay on the map (retention/audit); the detail view only
ever surfaces the review whose key matches the current revision. Small, seam-only
helpers live here rather than in the analysis engine to avoid a circular import
between the store and the API layer.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

ReviewDecision = Literal["correct", "incorrect", "uncertain"]


def _raw_finding(finding: object) -> object:
    """A finding as it was analysed, without any API-enriched ``review`` key.

    Defensive: the revision must identify the machine analysis, so an
    accidentally enriched finding hashes the same as the raw one it came from.
    """
    if isinstance(finding, dict) and "review" in finding:
        return {k: v for k, v in finding.items() if k != "review"}
    return finding


def compute_analysis_revision(*, content_hash: str, analysis_version: str | None, analysis: list | None) -> str:
    """Deterministic id of the exact analysis a reviewer is looking at."""
    material = json.dumps(
        {
            "content_hash": content_hash,
            "analysis_version": analysis_version,
            "analysis": [_raw_finding(f) for f in (analysis or [])],
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def review_key(revision: str, index: int) -> str:
    return f"{revision}:{index}"


def reviews_for_current_revision(reviews: dict | None, revision: str, count: int) -> list[dict | None]:
    """Per-finding review for the CURRENT revision only, indexed ``0..count-1``.

    Entries stored under a previous revision are preserved on ``reviews`` but do
    not appear here — a review only describes the analysis it was made against.
    """
    reviews = reviews or {}
    return [reviews.get(review_key(revision, i)) for i in range(count)]
