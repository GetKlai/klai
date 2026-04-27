"""CSRF exempt rationale lint test — SPEC-SEC-CORS-001 REQ-4, AC-12.

Parses `app/middleware/session.py` with the stdlib `ast` module and
verifies that every string literal in `_CSRF_EXEMPT_PREFIXES` is preceded
(within 5 source lines) by at least one comment line containing:
  (a) a keyword from the rationale set, AND
  (b) a REQ-N or AC-N reference.

Failure message names the offending prefix so the developer knows
exactly which entry to fix.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_SESSION_PY = (
    pathlib.Path(__file__).parent.parent
    / "app"
    / "middleware"
    / "session.py"
)

# At least one of these keywords must appear in a preceding comment (AC-12)
_RATIONALE_KEYWORDS = {
    "pre-session",
    "no session",
    "sendBeacon",
    "internal",
    "partner",
    "widget",
    "Zitadel",
    "signup",
    "health probe",
}

# The comment must also contain a REQ-N or AC-N reference. Supports
# REQ-10+, sub-section forms like REQ-1.6, and AC-99 (forward-compat for
# SPECs that grow more than 9 requirement groups or 99 acceptance criteria).
_REQ_AC_PATTERN = re.compile(r"\b(REQ-\d+(?:\.\d+)?|AC-\d+)\b")

# Canonical trailing-line format enforced across all entries to prevent
# review-fatigue drift. Matches lines like:
#   # REQ-1.2 / AC-2
#   # REQ-3.1 / AC-9, AC-11
#   # REQ-4.3 / AC-2
# The whole comment block is allowed to contain free-form rationale, but the
# LAST comment line within the lookback window must match this exact shape.
_CANONICAL_TRAILING_PATTERN = re.compile(
    r"^#\s*REQ-\d+(?:\.\d+)?\s*/\s*AC-\d+(?:\s*,\s*AC-\d+)*\s*$"
)

# Maximum number of lines above the literal to search
_LOOKBACK = 5


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _extract_prefixes_with_lines(source: str, tree: ast.AST) -> list[tuple[str, int]]:
    """Return (prefix_string, line_number) for every literal in _CSRF_EXEMPT_PREFIXES.

    Handles both plain Assign and annotated AnnAssign (e.g. `x: tuple[str, ...] = (...)`).
    """
    results: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        value: ast.expr | None = None

        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_CSRF_EXEMPT_PREFIXES"
        ):
            value = node.value

        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "_CSRF_EXEMPT_PREFIXES"
            and node.value is not None
        ):
            value = node.value

        if value is not None and isinstance(value, ast.Tuple):
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    results.append((elt.value, elt.lineno))

    return results


def _preceding_comment_lines(source_lines: list[str], prefix_lineno: int) -> list[str]:
    """Return up to _LOOKBACK comment lines immediately above prefix_lineno (1-based)."""
    comments = []
    start = max(0, prefix_lineno - 1 - _LOOKBACK)
    end = prefix_lineno - 1  # exclusive; line at prefix_lineno is the literal itself
    for i in range(start, end):
        stripped = source_lines[i].strip()
        if stripped.startswith("#"):
            comments.append(stripped)
    return comments


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_csrf_exempt_prefixes_have_rationale() -> None:
    """AC-12: Every _CSRF_EXEMPT_PREFIXES entry has inline rationale.

    REQ-4.1 / REQ-4.2 — each entry must be preceded (within 5 lines) by:
    - A comment with at least one rationale keyword AND
    - A REQ-N or AC-N reference in that same comment block.
    """
    assert _SESSION_PY.exists(), f"session.py not found at {_SESSION_PY}"

    source = _SESSION_PY.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(_SESSION_PY))

    prefixes = _extract_prefixes_with_lines(source, tree)
    assert prefixes, "Could not find _CSRF_EXEMPT_PREFIXES in session.py — check AST walker"

    failures: list[str] = []

    for prefix_value, lineno in prefixes:
        comments = _preceding_comment_lines(source_lines, lineno)

        if not comments:
            failures.append(
                f"  Prefix {prefix_value!r} (line {lineno}): "
                "no comment found within 5 lines above"
            )
            continue

        combined = " ".join(comments)

        has_keyword = any(kw in combined for kw in _RATIONALE_KEYWORDS)
        has_req_ac = bool(_REQ_AC_PATTERN.search(combined))

        if not has_keyword:
            failures.append(
                f"  Prefix {prefix_value!r} (line {lineno}): "
                f"comment lacks a rationale keyword from {sorted(_RATIONALE_KEYWORDS)}. "
                f"Found: {combined!r}"
            )
        if not has_req_ac:
            failures.append(
                f"  Prefix {prefix_value!r} (line {lineno}): "
                f"comment lacks a REQ-N or AC-N reference. "
                f"Found: {combined!r}"
            )

    if failures:
        pytest.fail(
            "The following _CSRF_EXEMPT_PREFIXES entries lack inline rationale "
            "(REQ-4.1 / REQ-4.2 / AC-12):\n"
            + "\n".join(failures)
        )


def test_csrf_exempt_rationale_format_is_canonical() -> None:
    """REQ-4.1 follow-up: enforce the canonical trailing-line format.

    Each entry's comment block MUST end (within the 5-line lookback) with a
    standalone line like ``# REQ-X.Y / AC-Z`` or ``# REQ-X.Y / AC-A, AC-B``.

    The free-form rationale lines above are unconstrained; only the trailing
    line is canonical. This prevents the format drift the reviewer flagged
    in PR #180 simplify-pass (e.g. mixing ``REQ-1 / AC-2`` with ``REQ-1
    REQ-4.3 — narrative ... AC-2`` made it impossible to grep for
    "every entry that references REQ-3.1").
    """
    assert _SESSION_PY.exists(), f"session.py not found at {_SESSION_PY}"

    source = _SESSION_PY.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(_SESSION_PY))

    prefixes = _extract_prefixes_with_lines(source, tree)
    assert prefixes, "Could not find _CSRF_EXEMPT_PREFIXES in session.py"

    failures: list[str] = []

    for prefix_value, lineno in prefixes:
        comments = _preceding_comment_lines(source_lines, lineno)
        if not comments:
            failures.append(
                f"  Prefix {prefix_value!r} (line {lineno}): no comment block."
            )
            continue

        last_line = comments[-1]
        if not _CANONICAL_TRAILING_PATTERN.match(last_line):
            failures.append(
                f"  Prefix {prefix_value!r} (line {lineno}): trailing comment "
                f"line is not canonical. Expected `# REQ-X.Y / AC-Z` (or "
                f"`AC-A, AC-B` for multiple ACs). Got: {last_line!r}"
            )

    if failures:
        pytest.fail(
            "The following _CSRF_EXEMPT_PREFIXES entries do not end with a "
            "canonical `# REQ-X.Y / AC-Z` trailing comment line:\n"
            + "\n".join(failures)
        )
