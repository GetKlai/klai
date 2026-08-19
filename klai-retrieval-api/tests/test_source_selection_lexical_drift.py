"""Prevent lexical source-label matching from returning to source selection."""

from __future__ import annotations

import ast
from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[1] / "retrieval_api" / "services"
_SOURCE_SELECTION_PATHS = tuple(sorted(_SERVICE_ROOT.glob("*.py")))
_REMOVED_SYMBOLS = (
    "STOP_WORDS",
    "_detect_mentioned_sources",
    "_build_keyword_map",
    "layer1_keyword",
)


def _query_substring_comparisons(source: str) -> list[int]:
    tree = ast.parse(source)

    def names(expr: ast.AST) -> set[str]:
        return {node.id for node in ast.walk(expr) if isinstance(node, ast.Name)}

    def is_query_name(name: str) -> bool:
        return "query" in name.lower() or name.lower() in {"q", "text", "user_input"}

    def is_source_name(name: str) -> bool:
        lowered = name.lower()
        return "source_label" in lowered or lowered in {"label", "token", "term", "keyword"}

    query_names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and is_query_name(node.id)
    }
    source_names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and is_source_name(node.id)
    }
    assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)]
    for _ in assignments:
        for assignment in assignments:
            target_names = {name for target in assignment.targets for name in names(target)}
            value_names = names(assignment.value)
            if value_names & query_names:
                query_names.update(target_names)
            if value_names & source_names:
                source_names.update(target_names)

    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Compare)
        and any(isinstance(operator, ast.In) for operator in node.ops)
        and names(node.left) & source_names
        and any(names(comparator) & query_names for comparator in node.comparators)
    ]


def test_guard_detects_reintroduced_query_substring_match() -> None:
    dangerous = "def select(term, query_resolved):\n    return term in query_resolved.lower()\n"
    assert _query_substring_comparisons(dangerous) == [2]


def test_guard_detects_query_alias_and_common_parameter_names() -> None:
    dangerous = (
        "def select(source_label, user_input):\n"
        "    normalized = user_input.lower()\n"
        "    token = source_label.split('.')[0]\n"
        "    return token in normalized\n"
    )
    assert _query_substring_comparisons(dangerous) == [4]


def test_guard_scans_every_service_module() -> None:
    assert _SOURCE_SELECTION_PATHS == tuple(sorted(_SERVICE_ROOT.glob("*.py")))
    assert len(_SOURCE_SELECTION_PATHS) > 2


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
