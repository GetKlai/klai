r"""SPEC-SEC-HYGIENE-001 REQ-21 / AC-21: `_safe_return_to` rejects backslash
and percent-encoded protocol-relative URLs.

The pre-fix function rejected only `//`-prefix and `://`-anywhere. An
attacker could bypass it with:
- `/\evil.com` — browser normalises backslash to forward-slash
- `/%2fevil.com` — percent-decoded to `//evil.com`
- `/\\evil.com` — double-backslash also browser-normalised

This test parametrises every input the AC-21 table specifies and asserts
the safe output. The function MUST return the ORIGINAL (non-decoded)
value when the checks pass — verified by the last two parametrised rows
that contain `%`-encoded segments which decode to safe forms.
"""

from __future__ import annotations

import pytest

from app.api.auth_bff import _safe_return_to


@pytest.mark.parametrize(
    "value, expected",
    [
        # Attack vectors → /app fallback
        ("/\\evil.com", "/app"),
        ("/%2fevil.com", "/app"),
        ("/%2Fevil.com", "/app"),  # case insensitive
        ("/\\\\evil.com", "/app"),
        ("//evil.com", "/app"),
        ("https://evil.com", "/app"),
        ("javascript:alert(1)", "/app"),  # no leading /
        ("", "/app"),
        # Legitimate paths → unchanged ORIGINAL value (REQ-21.3)
        ("/app/dashboard", "/app/dashboard"),
        ("/app/dashboard?foo=bar%20baz", "/app/dashboard?foo=bar%20baz"),
        ("/app/path%2Fsub", "/app/path%2Fsub"),
    ],
)
def test_safe_return_to_parametrised(value: str, expected: str) -> None:
    """Covers REQ-21.1, REQ-21.2, REQ-21.3, REQ-21.4."""
    assert _safe_return_to(value) == expected


def test_safe_return_to_none_returns_app() -> None:
    """REQ-21.2 — None-equivalent (the function accepts str typing but
    callers may pass None via `request.query_params.get(...)`).
    The implementation must treat falsy values as the safe-default branch.
    """
    # type: ignore[arg-type] — deliberately exercise the falsy guard.
    assert _safe_return_to(None) == "/app"  # type: ignore[arg-type]
