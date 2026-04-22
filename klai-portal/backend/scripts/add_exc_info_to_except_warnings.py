"""One-shot codemod: add `exc_info=True` to logger.warning/error calls
inside `except Exception` blocks where it's missing.

Strategy:
1. Walk every .py file in app/.
2. Parse AST, locate the offending Call nodes (same logic as
   tests/test_logger_traceback_audit.py).
3. For each offender, do a text edit on the source: insert
   `, exc_info=True` before the closing `)` of the call.

Idempotent: skips calls that already have exc_info=. Run once, then
the test_logger_traceback_audit pytest pins it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND_APP = Path(__file__).parent.parent / "app"
EXCLUDED_PATH_PARTS = (
    "/tests/",
    "/alembic/",
    "/scripts/",
    "/.venv/",
    "__pycache__",
)


def _is_logger_call(node: ast.Call, methods: set[str]) -> bool:
    if not isinstance(node.func, ast.Attribute) or node.func.attr not in methods:
        return False
    target = node.func.value
    while isinstance(target, ast.Attribute):
        target = target.value
    return isinstance(target, ast.Name) and target.id in {"logger", "log", "_logger", "LOGGER"}


def _has_exc_info_kwarg(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "exc_info":
            if isinstance(kw.value, ast.Constant) and kw.value.value is False:
                return False
            return True
    return False


def _is_broad_except(node: ast.ExceptHandler) -> bool:
    if node.type is None:
        return True
    if isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}:
        return True
    if isinstance(node.type, ast.Tuple):
        for elt in node.type.elts:
            if isinstance(elt, ast.Name) and elt.id in {"Exception", "BaseException"}:
                return True
    return False


class _OffenderFinder(ast.NodeVisitor):
    def __init__(self) -> None:
        self.offenders: list[ast.Call] = []
        self._depth = 0

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if not _is_broad_except(node):
            self.generic_visit(node)
            return
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        if (
            self._depth > 0
            and _is_logger_call(node, methods={"warning", "error", "warn"})
            and not _has_exc_info_kwarg(node)
        ):
            self.offenders.append(node)
        self.generic_visit(node)


def _patch_file(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return 0
    finder = _OffenderFinder()
    finder.visit(tree)
    if not finder.offenders:
        return 0

    # ast end_col_offset / end_lineno point at the closing `)`. We process
    # offenders bottom-up so earlier edits don't shift later positions.
    lines = source.splitlines(keepends=True)
    offenders_sorted = sorted(finder.offenders, key=lambda n: (n.end_lineno or 0, n.end_col_offset or 0), reverse=True)
    for call in offenders_sorted:
        end_line_idx = (call.end_lineno or 1) - 1
        end_col = call.end_col_offset or 0
        if end_line_idx >= len(lines):
            continue
        line = lines[end_line_idx]
        # end_col points just past the `)`. Defensive: skip if column lies
        # outside the line (CRLF vs LF mismatches, escaped chars, etc.).
        if end_col == 0 or end_col > len(line) or line[end_col - 1] != ")":
            continue
        needs_comma = bool(call.args) or bool(call.keywords)
        insertion = ", exc_info=True" if needs_comma else "exc_info=True"
        new_line = line[: end_col - 1] + insertion + line[end_col - 1 :]
        lines[end_line_idx] = new_line

    path.write_text("".join(lines), encoding="utf-8")
    return len(finder.offenders)


def main() -> int:
    total = 0
    files_changed = 0
    for path in BACKEND_APP.rglob("*.py"):
        as_str = str(path).replace("\\", "/")
        if any(part in as_str for part in EXCLUDED_PATH_PARTS):
            continue
        n = _patch_file(path)
        if n:
            files_changed += 1
            total += n
            print(f"  patched {n} call(s) in {path.relative_to(BACKEND_APP.parent)}")
    print(f"\nDone: {total} call(s) across {files_changed} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
