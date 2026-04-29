"""HY-46 (stub) — page_path validation rejects URL-encoded + fullwidth bypass.

SPEC-SEC-HYGIENE-001 REQ-46.1.

Conservative-by-default: the validator rejects any path that contains a literal
``%`` (catches URL-encoded variants ``%2e%2e``, ``%2f``, ``%20``, etc.) AND any
path whose Unicode-NFKC-normalised form contains ``..``, ``\\``, or starts with
``/``. The legitimate caller (LibreChat) generates page paths from text — it
has no reason to URL-encode or to use fullwidth Unicode glyphs.

Out of scope (deferred to follow-up SPEC per REQ-46.3): full encoding matrix
including overlong UTF-8, IDN homoglyphs, and the klai-docs route-handler audit
that determines downstream blast radius (REQ-46.2).
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("page_path", "expect_ok"),
    [
        # accepted shapes
        ("docs/section/page", True),
        ("docs/has-dash", True),
        # literal traversal
        ("../etc/passwd", False),
        # URL-encoded traversal
        ("%2e%2e/passwd", False),
        ("%2E%2E/passwd", False),
        ("%2f%2fevil", False),
        # any percent triggers reject (REQ-46.1, conservative)
        ("docs/has%20space", False),
        # fullwidth U+FF0E FULL STOP that NFKC-normalises to ".."
        ("．．/etc/passwd", False),  # noqa: RUF001 — intentional ambiguous glyph (test fixture)
        # backslash + leading slash
        ("docs/sub\\evil", False),
        ("/absolute", False),
    ],
)
def test_validate_page_path(page_path: str, expect_ok: bool) -> None:
    """REQ-46.1 — accept legitimate paths, reject all 4 bypass classes."""
    from main import _validate_page_path

    if expect_ok:
        # No exception = accepted. The helper returns None.
        _validate_page_path(page_path)
    else:
        with pytest.raises(ValueError):
            _validate_page_path(page_path)


def test_full_encoding_matrix_is_deferred() -> None:
    """REQ-46.3 — overlong UTF-8 / IDN homoglyph / klai-docs audit is a stub.

    This test exists as a documentation marker: a future SPEC ships the full
    matrix once the klai-docs route-handler audit (REQ-46.2) determines the
    actual blast radius downstream. Fail this test if anyone narrows the
    deferred scope without a follow-up SPEC reference.
    """
    from main import _validate_page_path

    docstring = (_validate_page_path.__doc__ or "").lower()
    assert "stub" in docstring or "deferred" in docstring, (
        "_validate_page_path docstring must mark the deferred encoding scope "
        "(REQ-46.2/46.3) so the next reader knows where the rest lives"
    )
