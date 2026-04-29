"""SPEC-SEC-HYGIENE-001 REQ-23 / AC-23: widget_config docstring + MX:REASON
explicitly state that the Origin check is UX-gating, not a security boundary.

The Cornelis audit re-filed Origin as a partial finding because the header
is spoofable by non-browser clients. The correct disposition is "documented
acceptance" — Origin is a UX hint that stops a different tenant's site from
embedding this widget; the actual security boundary is the HS256 JWT
session_token. Document it so the next audit doesn't re-file the same item.

Tests (docs-only): assertions on the docstring + the @MX:REASON comment.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.api.partner import widget_config


def test_widget_config_docstring_exists() -> None:
    """REQ-23.1: docstring is non-empty (we are about to assert content)."""
    assert widget_config.__doc__ is not None
    assert widget_config.__doc__.strip() != ""


def test_widget_config_docstring_mentions_origin() -> None:
    """REQ-23.1: docstring must talk about the Origin header explicitly."""
    assert "Origin" in widget_config.__doc__  # type: ignore[operator]


def test_widget_config_docstring_calls_out_ux_gating() -> None:
    """REQ-23.1: at least one of the canonical phrases must appear so the
    docstring's risk framing matches the SPEC-SEC-HYGIENE-001 disposition.
    """
    doc = widget_config.__doc__ or ""
    doc_lower = doc.lower()
    expected_any = ("ux-only", "ux only", "not a security boundary", "ux-gating")
    assert any(phrase in doc_lower for phrase in expected_any), (
        f"widget_config docstring must mention one of {expected_any!r} so "
        "the next audit knows the Origin check is intentional UX-gating, "
        f"not a security boundary. Current doc:\n{doc}"
    )


def test_widget_config_docstring_mentions_widget_id() -> None:
    """REQ-23.1: docstring must call out widget_id as the primary identifier."""
    assert "widget_id" in (widget_config.__doc__ or "")


def test_widget_config_docstring_mentions_jwt_security() -> None:
    """REQ-23.1: docstring must call out the JWT / session_token as the
    primary security mechanism downstream of the Origin UX gate.
    """
    doc = widget_config.__doc__ or ""
    assert "JWT" in doc or "session_token" in doc, (
        "widget_config docstring must mention JWT or session_token as the "
        "primary security mechanism. Current doc:\n" + doc
    )


def test_widget_config_mx_reason_references_docstring() -> None:
    """REQ-23.3: the @MX:REASON line near widget_config must reference the
    docstring clarification. We accept any of: 'UX-only', 'see docstring',
    'UX-gating'.
    """
    src = Path(inspect.getfile(widget_config)).read_text(encoding="utf-8")
    # Find the @MX:REASON line that is associated with widget_config —
    # the function definition is the marker; we read forward a few lines
    # because the comment block lives just inside the function body in
    # this codebase.
    lines = src.splitlines()
    func_idx = next(
        (i for i, line in enumerate(lines) if "async def widget_config(" in line),
        -1,
    )
    assert func_idx >= 0, "Could not locate widget_config in source"

    # Inspect the docstring + comment block (next ~30 lines).
    block = "\n".join(lines[func_idx : func_idx + 35])
    # First check the @MX:REASON comment exists in this block.
    assert "@MX:REASON" in block, (
        "widget_config block must carry an @MX:REASON comment "
        "(REQ-23.3)."
    )
    # Then check the reason text references the docstring.
    block_lower = block.lower()
    assert any(
        phrase in block_lower
        for phrase in ("ux-only", "ux only", "see docstring", "ux-gating")
    ), (
        "widget_config @MX:REASON / docstring must include a phrase that "
        "ties the Origin check to the docstring's UX-gating clarification "
        "(REQ-23.3). See SPEC-SEC-HYGIENE-001 REQ-23 for accepted phrases."
    )
