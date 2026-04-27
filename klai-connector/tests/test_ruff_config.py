"""SPEC-SEC-HYGIENE-001 REQ-30.3 — ruff F821 enforcement contract.

The original HY-30 bug was a missing FastAPI import that turned every
"connector not found" response into a 500 (a UUID-existence oracle).
ruff rule F821 (undefined name) catches this class of bug at lint time.

This test pins the contract that the connector's ruff config selects
the F (Pyflakes) rule family, which includes F821, so a future regression
would fail CI before merging.

If the lint config drops F or replaces it with a narrower allowlist that
omits F821, this test fails — flagging that the safety net is gone.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def _load_pyproject() -> dict[str, object]:
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    return tomllib.loads(pyproject_path.read_text(encoding="utf-8"))


def test_ruff_select_enables_f821() -> None:
    """The 'F' rule family (or F821 explicitly) must be selected.

    F is shorthand for the entire Pyflakes ruleset; F821 is the specific
    "undefined name" check. Either form satisfies REQ-30.3.
    """
    config = _load_pyproject()
    select = config["tool"]["ruff"]["lint"]["select"]  # type: ignore[index]
    assert isinstance(select, list)
    assert "F" in select or "F821" in select, (
        "ruff lint.select must include 'F' (or 'F821' explicitly) so "
        "undefined-name regressions like SPEC-SEC-HYGIENE-001 HY-30 "
        "fail CI."
    )


def test_ruff_does_not_ignore_f821() -> None:
    """F821 must not be on the ignore list (defensive — caught even if added)."""
    config = _load_pyproject()
    ignore = config["tool"]["ruff"]["lint"].get("ignore", [])  # type: ignore[union-attr]
    assert "F821" not in ignore, (
        "F821 must not be ignored — see SPEC-SEC-HYGIENE-001 REQ-30.3."
    )
