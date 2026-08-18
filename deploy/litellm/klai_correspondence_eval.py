"""Pure, fast-CI-testable core of the pasted-correspondence distillation eval
harness (SPEC-RAG-CORRESPONDENCE-DISTILL-001 REQ-6/AC-6).

This module has NO network dependency — it only loads canaries from the
shared knowledge-ingest eval suite YAML, matches retrieval-api chunks against
their expected markers, and aggregates pass/fail across repeated samples. The
live invocation (real distillation call + real /retrieve call) lives in
scripts/eval_pasted_correspondence_live.py, which imports this module but is
itself a manually-run, opt-in script — it needs real Mistral quota and
network access to retrieval-api's Docker-internal hostname, so it cannot run
in standard CI (same constraint the existing knowledge-ingest eval harness
already has: knowledge_ingest.config.Settings.retrieval_api_url defaults to
http://retrieval-api:8040).

Chunk matching mirrors klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py's
_expected_chunk_canary: case-insensitive substring matching against strong
fields (title/url/id) always, against body text only when the marker is
specific enough to avoid generic false-positive hits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_MIN_BODY_CANARY_CHARS = 16


@dataclass(frozen=True)
class CorrespondenceCanary:
    id: str
    org_zitadel_id: str
    query: str
    expected_chunks: list[str] = field(default_factory=list)


def load_pasted_correspondence_canaries(
    suite_path: Path,
) -> list[CorrespondenceCanary]:
    """Load every query tagged mix: pasted_correspondence from a chat.yaml-shaped suite."""
    if not suite_path.exists():
        raise FileNotFoundError(f"eval suite not found: {suite_path}")

    data = yaml.safe_load(suite_path.read_text(encoding="utf-8")) or {}
    canaries: list[CorrespondenceCanary] = []
    for entry in data.get("queries", []):
        if entry.get("mix") != "pasted_correspondence":
            continue
        canaries.append(
            CorrespondenceCanary(
                id=entry["id"],
                org_zitadel_id=str(entry["org_zitadel_id"]),
                query=entry["query"],
                expected_chunks=list(entry.get("expected_chunks") or []),
            )
        )

    # Sol delta-review Fix 6: a canary with no expected_chunks can never fail
    # a real eval run — chunk_matches_expected has nothing to check a
    # retrieved chunk against — so it vacuously "passes" regardless of
    # retrieval quality. Fail loudly at load time instead of shipping a
    # canary that silently never catches a regression.
    empty = [c.id for c in canaries if not c.expected_chunks]
    if empty:
        raise ValueError(
            "pasted_correspondence canaries with empty expected_chunks "
            f"(would vacuously pass every eval run): {empty}"
        )

    return canaries


def _chunk_fields(chunk: dict[str, Any]) -> tuple[str, str]:
    strong_values = [
        chunk.get("chunk_id"),
        chunk.get("id"),
        chunk.get("title"),
        chunk.get("source_url"),
        chunk.get("url"),
    ]
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        strong_values.extend(
            [
                metadata.get("title"),
                metadata.get("source_url"),
                metadata.get("path"),
            ]
        )
    strong = "\n".join(str(v) for v in strong_values if v is not None).lower()
    body = str(chunk.get("text") or "").lower()
    return strong, body


def _allows_body_match(expected: str) -> bool:
    marker = expected.strip()
    return len(marker) >= _MIN_BODY_CANARY_CHARS and any(ch.isspace() for ch in marker)


def chunk_matches_expected(expected: str, chunk: dict[str, Any]) -> bool:
    """True if a single retrieval-api chunk satisfies one expected_chunks marker."""
    needle = expected.strip().lower()
    if not needle:
        return False
    strong, body = _chunk_fields(chunk)
    if needle in strong:
        return True
    return _allows_body_match(expected) and needle in body


def summarize_canary_samples(canary_id: str, sample_pass: list[bool]) -> dict[str, Any]:
    """Aggregate repeated pass/fail samples for one canary into a pass-rate summary."""
    if not sample_pass:
        raise ValueError("summarize_canary_samples requires at least one sample")

    total = len(sample_pass)
    passed = sum(1 for ok in sample_pass if ok)
    pass_rate = passed / total
    return {
        "canary_id": canary_id,
        "total": total,
        "passed": passed,
        "pass_rate": pass_rate,
        "majority_pass": pass_rate > 0.5,
    }
