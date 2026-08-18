"""Detect drift between the litellm-vendored eval suite and the canonical one.

``deploy/litellm/eval_suites/chat.yaml`` is a vendored copy of
``klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml`` (synced by
the deploy workflow so ``deploy/litellm/scripts/eval_pasted_correspondence_live.py``
can read it inside the litellm container without a cross-service mount into
knowledge-ingest's source tree). Same class of drift risk as
``test_klai_llm_throttle_drift.py`` — byte-identity must be enforced by a
test, not by hoping the sync workflow never misses a run.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PATH = (
    _REPO_ROOT
    / "klai-knowledge-ingest"
    / "knowledge_ingest"
    / "eval"
    / "suites"
    / "chat.yaml"
)
_VENDORED_PATH = _REPO_ROOT / "deploy" / "litellm" / "eval_suites" / "chat.yaml"


def test_litellm_vendored_chat_yaml_matches_canonical() -> None:
    assert _CANONICAL_PATH.exists()
    assert _VENDORED_PATH.exists()
    assert _VENDORED_PATH.read_bytes() == _CANONICAL_PATH.read_bytes()
