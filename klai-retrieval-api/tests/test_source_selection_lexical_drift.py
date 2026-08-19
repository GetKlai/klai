"""Prevent lexical source-label matching from returning to source selection."""

from __future__ import annotations

import ast
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "retrieval_api" / "services"
_SOURCE_SELECTION_PATHS = (
    _SERVICE_ROOT / "diversity.py",
    _SERVICE_ROOT / "router.py",
)
_REMOVED_SYMBOLS = (
    "STOP_WORDS",
    "_detect_mentioned_sources",
    "_build_keyword_map",
    "layer1_keyword",
)


def _query_substring_comparisons(source: str) -> list[int]:
    offenders: list[int] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            if not isinstance(operator, ast.In):
                continue
            compared_text = ast.unparse(comparator).lower()
            if "query" in compared_text:
                offenders.append(node.lineno)
    return offenders


def test_guard_detects_reintroduced_query_substring_match() -> None:
    dangerous = "def select(term, query_resolved):\n    return term in query_resolved.lower()\n"
    assert _query_substring_comparisons(dangerous) == [2]


def test_source_selection_has_no_lexical_query_matching() -> None:
    offenders: dict[str, list[int]] = {}
    for path in _SOURCE_SELECTION_PATHS:
        source = path.read_text(encoding="utf-8")
        for symbol in _REMOVED_SYMBOLS:
            assert symbol not in source, f"Removed lexical selector {symbol} returned in {path}"
        lines = _query_substring_comparisons(source)
        if lines:
            offenders[path.name] = lines

    assert not offenders, (
        "Source-selection modules must compare candidates semantically; they may not "
        f"select a source by substring-matching query text: {offenders}"
    )
