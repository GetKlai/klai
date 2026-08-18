"""Mechanical guard: never combine a truncated evidence render with label stripping.

``klai_citations.render_evidence_context(max_chars=...)`` drops whole trailing
entries — labels after the cut are never shown to the model. But
``evidence_label_ids`` (and ``compose_answer_with_trusted_sources``, which
calls it internally) derives the strippable-label set from the FULL chunk
list. A caller that renders truncated and strips untruncated on the same
chunk list silently treats never-rendered labels as legitimate, hiding
hallucinated citations. See the docstrings on both functions in
klai-libs/citations and the pinned-limitation test
``test_evidence_label_ids_does_not_model_max_chars_truncation``.

No current caller combines the two (verified 2026-08-18: retrieval-api's
synthesis.py truncates but never strips; litellm and partner_chat strip but
never truncate). This test keeps it that way, same source-scan pattern as
``test_direct_mistral_throttle_drift.py`` — a comment/docstring cannot stop
the drift, a failing test can.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Every directory that consumes klai_citations in production code.
_CONSUMER_DIRS = [
    _REPO_ROOT / "deploy" / "litellm",
    _REPO_ROOT / "klai-portal" / "backend" / "app",
    _REPO_ROOT / "klai-retrieval-api" / "retrieval_api",
]

# A render_evidence_context(...) call whose argument list carries max_chars.
_TRUNCATED_RENDER_RE = re.compile(
    r"render_evidence_context\((?:[^()]|\([^()]*\))*?max_chars", re.DOTALL
)
_LABEL_CONSUMER_RE = re.compile(
    r"evidence_label_ids|compose_answer_with_trusted_sources"
)


def _is_offender(source: str) -> bool:
    return bool(_TRUNCATED_RENDER_RE.search(source)) and bool(
        _LABEL_CONSUMER_RE.search(source)
    )


def test_offender_rule_detects_the_dangerous_combination() -> None:
    dangerous = (
        "ctx = render_evidence_context(chunks, include_source_urls=True,\n"
        "    max_chars=24_000)\n"
        "ids = evidence_label_ids(chunks)\n"
    )
    assert _is_offender(dangerous) is True

    truncate_only = "ctx = render_evidence_context(chunks, max_chars=24_000)\n"
    assert _is_offender(truncate_only) is False

    strip_only = (
        "ctx = render_evidence_context(chunks)\n"
        "composed = compose_answer_with_trusted_sources(text, sources,\n"
        "    evidence_chunks=chunks)\n"
    )
    assert _is_offender(strip_only) is False

    # max_chars used for something unrelated in the same file (partner_chat
    # truncates history/page-context with its own max_chars variables).
    unrelated_max_chars = (
        "content = text[:max_chars]\nids = evidence_label_ids(chunks)\n"
    )
    assert _is_offender(unrelated_max_chars) is False


def test_no_production_file_combines_truncated_render_with_label_stripping() -> None:
    offenders: list[str] = []
    for consumer_dir in _CONSUMER_DIRS:
        for path in sorted(consumer_dir.rglob("*.py")):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            if _is_offender(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(_REPO_ROOT)))

    assert not offenders, (
        "These files combine render_evidence_context(max_chars=...) with a "
        "label-consuming call (evidence_label_ids / "
        "compose_answer_with_trusted_sources). The truncated render drops "
        "trailing evidence labels that the label set still reports as "
        "strippable — pass the RENDERED subset of chunks to the "
        f"label-consuming call instead: {offenders}"
    )
