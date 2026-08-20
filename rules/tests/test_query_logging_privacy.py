"""Guard direct query log arguments behind full tenant telemetry."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QUERY_LOG_SOURCES = (
    REPO_ROOT / "deploy/litellm/klai_knowledge.py",
    REPO_ROOT / "klai-portal/backend/app/services/gap_rescorer.py",
)
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
QUERY_NAMES = {"query", "query_text", "raw_query", "rewritten_query", "dropped"}


def _identifier(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _contains_query_text(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and _identifier(node.func) == "len":
        return False
    return any(
        (_identifier(child) or "").lower() in QUERY_NAMES for child in ast.walk(node)
    )


def _is_full_telemetry_gate(node: ast.AST) -> bool:
    if (
        not isinstance(node, ast.Compare)
        or len(node.ops) != 1
        or len(node.comparators) != 1
    ):
        return False
    if not isinstance(node.ops[0], ast.Eq):
        return False
    sides = (node.left, node.comparators[0])
    return {_identifier(side) for side in sides} >= {"telemetry_level"} and any(
        isinstance(side, ast.Constant) and side.value == "full" for side in sides
    )


def _safe_conditional(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.IfExp)
        and _is_full_telemetry_gate(node.test)
        and _contains_query_text(node.body)
        and not _contains_query_text(node.orelse)
    )


def _inside_full_telemetry_gate(
    call: ast.Call, parents: dict[ast.AST, ast.AST]
) -> bool:
    current: ast.AST = call
    while current in parents:
        parent = parents[current]
        if (
            isinstance(parent, ast.If)
            and current in parent.body
            and _is_full_telemetry_gate(parent.test)
        ):
            return True
        current = parent
    return False


def _query_log_violations(source: str) -> list[int]:
    tree = ast.parse(source)
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    violations: list[int] = []

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if (
            not isinstance(call.func, ast.Attribute)
            or call.func.attr not in LOG_METHODS
            or _identifier(call.func.value) != "logger"
        ):
            continue
        query_args = [arg for arg in call.args if _contains_query_text(arg)]
        query_args.extend(
            keyword.value
            for keyword in call.keywords
            if _contains_query_text(keyword.value)
        )
        if not query_args or _inside_full_telemetry_gate(call, parents):
            continue
        if not all(_safe_conditional(arg) for arg in query_args):
            violations.append(call.lineno)

    return violations


@pytest.mark.parametrize(
    "statement",
    [
        'logger.info("query=%r", query)',
        'logger.info(f"query={query}")',
        'logger.info("query=%r", query if telemetry_level == "full" or debug else "<redacted>")',
    ],
)
def test_guard_rejects_unguarded_query_logging(statement: str) -> None:
    assert _query_log_violations(
        f"def handle(query, telemetry_level, debug):\n    {statement}\n"
    ) == [2]


def test_guard_accepts_full_telemetry_conditional() -> None:
    source = """
def handle(query, telemetry_level):
    logger.info("query=%r", query if telemetry_level == "full" else "<redacted>")
"""
    assert _query_log_violations(source) == []


@pytest.mark.parametrize("source_path", QUERY_LOG_SOURCES, ids=lambda path: path.name)
def test_direct_query_logs_require_full_telemetry(source_path: Path) -> None:
    violations = _query_log_violations(source_path.read_text())
    assert violations == [], (
        f"{source_path.relative_to(REPO_ROOT)} directly logs query text without "
        f"telemetry_level == 'full' at lines {violations}"
    )
