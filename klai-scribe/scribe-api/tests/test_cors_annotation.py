"""HY-38 / REQ-38 — CORS regex MX:WARN annotation regression.

Docs-only finding. The permissive `allow_origin_regex + allow_credentials`
combination is currently safe ONLY because scribe is back-end-only and not
browser-reachable. Annotate the registration site so a future change that
exposes scribe to browsers triggers a review against SPEC-SEC-CORS-001.

See SPEC-SEC-HYGIENE-001 REQ-38.
"""
from __future__ import annotations

from pathlib import Path

_MAIN_PY = Path(__file__).parent.parent / "app" / "main.py"


def _read_main() -> str:
    return _MAIN_PY.read_text(encoding="utf-8")


def test_cors_middleware_present() -> None:
    """Sanity: the CORSMiddleware registration is still where we think it is."""
    src = _read_main()
    assert "CORSMiddleware" in src
    assert "allow_origin_regex" in src


def test_cors_has_mx_warn_above_registration() -> None:
    """REQ-38.1 + REQ-38.2 — MX:WARN annotation precedes the CORS call."""
    src = _read_main()
    cors_idx = src.index("app.add_middleware(\n    CORSMiddleware")  # exact site
    preceding = src[:cors_idx]
    # Take the last ~25 lines preceding so we don't pick up unrelated tags.
    preceding_window = "\n".join(preceding.splitlines()[-25:])

    assert "@MX:WARN" in preceding_window, (
        "CORSMiddleware registration MUST be preceded by @MX:WARN per REQ-38.2"
    )
    assert "@MX:REASON" in preceding_window, (
        "CORSMiddleware registration MUST be preceded by @MX:REASON per REQ-38.2"
    )


def test_cors_mx_reason_mentions_back_end_only() -> None:
    """REQ-38.2 — reason text MUST explain why permissive CORS is currently safe."""
    src = _read_main()
    # Locate the registration call, NOT the import line.
    cors_idx = src.index("add_middleware(\n    CORSMiddleware")
    preceding = src[:cors_idx]
    preceding_window = "\n".join(preceding.splitlines()[-25:]).lower()

    assert (
        "back-end-only" in preceding_window
        or "not browser-reachable" in preceding_window
    ), "@MX:REASON must mention 'back-end-only' or 'not browser-reachable'"


def test_cors_mx_reason_references_spec() -> None:
    """REQ-38.2 — reason text MUST reference the relevant SPEC(s)."""
    src = _read_main()
    # Locate the registration call, NOT the import line.
    cors_idx = src.index("add_middleware(\n    CORSMiddleware")
    preceding = src[:cors_idx]
    preceding_window = "\n".join(preceding.splitlines()[-25:])

    assert (
        "SPEC-SEC-HYGIENE-001 REQ-38" in preceding_window
        or "SPEC-SEC-CORS-001" in preceding_window
    )
