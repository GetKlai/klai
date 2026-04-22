"""One-shot codemod — ALREADY RUN ON 2026-04-22. DO NOT RE-RUN BLINDLY.
============================================================================

This script added `exc_info=True` to every `logger.warning` / `logger.error`
call inside an `except Exception` block where it was missing. It was run
once on 2026-04-22, patched 40 callsites across 20 files, and is kept in
the repo only for historical reference and as an example for the next
codemod of this shape.

From here on, `tests/test_logger_traceback_audit.py` pins the invariant:
any new offender fails CI. Fix at the call site, don't re-run this tool.

Known limitations (relevant if you DO want to re-run it):
  - Multi-line calls with a trailing comma before `)` produce invalid
    Python (`,\n, exc_info=True)`). Five files tripped this on the
    original run and were fixed by hand. A re-run would re-introduce
    those breakages.
  - The codemod preserves whatever separator comes before the closing
    `)`, so a call like `logger.warning("x")` (no args to append to)
    still works, but `logger.warning(\n    "x",\n)` (trailing comma,
    closing paren on its own line) does not.

If you genuinely need to re-run, first verify the codemod on a small
sample with `git diff` and run `uv run pytest tests/` before pushing.

============================================================================
Original docstring:

Strategy:
1. Walk every .py file in app/.
2. Parse AST, locate the offending Call nodes (same logic as
   tests/test_logger_traceback_audit.py).
3. For each offender, do a text edit on the source: insert
   `, exc_info=True` before the closing `)` of the call.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Safety interlock: require an explicit env flag before making any edits.
# Prevents accidental re-runs (e.g. tab-completion, rerun of a shell
# history entry) from re-introducing the multi-line syntax breakages
# that occurred on the original 2026-04-22 pass.
_SAFETY_ENV = "I_HAVE_READ_THE_ONE_SHOT_DOCSTRING"

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
    import os

    if os.environ.get(_SAFETY_ENV) != "1":
        sys.stderr.write(
            "REFUSING TO RUN: this is a one-shot codemod that was already\n"
            "applied on 2026-04-22. See the module docstring for why re-running\n"
            "is risky (multi-line calls with trailing commas produce invalid\n"
            f"Python). If you really want to proceed, set {_SAFETY_ENV}=1.\n"
        )
        return 2
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
