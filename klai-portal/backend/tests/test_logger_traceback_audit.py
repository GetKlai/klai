"""Static audit: every logger.warning / logger.error call inside an
`except Exception` block must capture a traceback.

Two acceptable patterns:

    except Exception:
        logger.warning("upstream_degraded", exc_info=True)

    except Exception:
        logger.exception("unexpected_failure")  # exc_info implicit

Forbidden:

    except Exception as exc:
        logger.warning("failed", error=str(exc))   # loses stack
    except Exception:
        logger.warning("failed")                    # loses stack and exc

Rationale: when the warning fires in production at 3am the operator has
no idea where it came from without the traceback. The fix is one keyword
argument, never controversial.

Ruff TRY401 catches `logger.exception(..., str(exc))` redundancy but not
this pattern, and TRY401 is `ignore`d in pyproject.toml anyway. This
test is the actual enforcement.

When CI fails on a new offender, fix the call site — almost never add
to ALLOWED_OFFENDERS.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

# Functions that we know intentionally swallow without traceback (very
# rare — only for spam-prone health-probe loops where the traceback would
# explode log volume on a known-down upstream). Each entry MUST have a
# justification comment.
ALLOWED_OFFENDERS: frozenset[tuple[str, str]] = frozenset(
    {
        # (file_basename, function_name)
        # No entries yet. Add only with a written justification.
    }
)

EXCLUDED_PATH_PARTS: tuple[str, ...] = (
    "/tests/",
    "/alembic/",
    "/scripts/",
    "/.venv/",
    "__pycache__",
)

BACKEND_APP: Path = Path(__file__).parent.parent / "app"


def _iter_python_files() -> Iterable[Path]:
    for path in BACKEND_APP.rglob("*.py"):
        as_str = str(path).replace("\\", "/")
        if any(part in as_str for part in EXCLUDED_PATH_PARTS):
            continue
        yield path


def _is_logger_call(node: ast.Call, methods: set[str]) -> bool:
    """True if this is `<something>.<method>(...)` for one of `methods`.

    Matches both `logger.warning(...)` and `app.api.x.logger.warning(...)`.
    """
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in methods:
        return False
    # The receiver is something with a name like 'logger' / 'log'.
    target = node.func.value
    while isinstance(target, ast.Attribute):
        target = target.value
    if not isinstance(target, ast.Name):
        return False
    return target.id in {"logger", "log", "_logger", "LOGGER"}


def _has_exc_info_kwarg(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "exc_info":
            # Accept any truthy literal (`True`) or any non-literal (the
            # caller may be passing a captured `exc`).
            if isinstance(kw.value, ast.Constant) and kw.value.value is False:
                return False
            return True
    return False


class _ExceptVisitor(ast.NodeVisitor):
    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path
        self.basename = source_path.name
        self.offending: list[tuple[int, str, str]] = []
        self._fn_stack: list[str] = []
        self._in_except_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._fn_stack.append(node.name)
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._fn_stack.append(node.name)
        self.generic_visit(node)
        self._fn_stack.pop()

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # Only flag broad `except` and `except Exception`. Specific
        # exceptions (httpx.ConnectError etc.) are intentional narrow
        # handlers where the caller knows what failed.
        if not self._is_broad_except(node):
            self.generic_visit(node)
            return
        self._in_except_depth += 1
        self.generic_visit(node)
        self._in_except_depth -= 1

    @staticmethod
    def _is_broad_except(node: ast.ExceptHandler) -> bool:
        if node.type is None:
            return True
        if isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}:
            return True
        # except (Exception, ...) tuples — count as broad if Exception is in there
        if isinstance(node.type, ast.Tuple):
            for elt in node.type.elts:
                if isinstance(elt, ast.Name) and elt.id in {"Exception", "BaseException"}:
                    return True
        return False

    def visit_Call(self, node: ast.Call) -> None:
        if self._in_except_depth > 0 and self._fn_stack:
            if _is_logger_call(node, methods={"warning", "error", "warn"}):
                if not _has_exc_info_kwarg(node):
                    fn_name = self._fn_stack[-1]
                    if (self.basename, fn_name) not in ALLOWED_OFFENDERS:
                        self.offending.append((node.lineno, fn_name, ast.unparse(node)[:140]))
        self.generic_visit(node)


def _audit_file(path: Path) -> list[tuple[Path, int, str, str]]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    visitor = _ExceptVisitor(path)
    visitor.visit(tree)
    return [(path, lineno, fn, snippet) for lineno, fn, snippet in visitor.offending]


def test_logger_warnings_in_except_capture_traceback():
    """`except Exception: logger.warning(...)` MUST include exc_info=True
    (or use `logger.exception(...)` which carries traceback by default).

    Production debugging requires the stack frame — string interpolation
    of the exception message is not sufficient.
    """
    offenders: list[tuple[Path, int, str, str]] = []
    for path in _iter_python_files():
        offenders.extend(_audit_file(path))

    if offenders:
        formatted = "\n".join(f"  {p}:{lineno} in {fn}()\n    {snippet}" for p, lineno, fn, snippet in offenders)
        pytest.fail(
            f"{len(offenders)} `logger.warning`/`logger.error` calls inside `except Exception` "
            "blocks are missing `exc_info=True`. Add the kwarg, or switch to "
            "`logger.exception(...)` for unexpected errors:\n" + formatted
        )
