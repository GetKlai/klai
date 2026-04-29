"""
Static analysis regression test — SPEC-SEC-AUDIT-2026-04 C1.

Verifies that every public-facing ``reverse_proxy`` block in the Caddyfile
strips all internal-trust headers before forwarding the request upstream.

Background
----------
No portal-api inbound endpoint currently reads ``X-Internal-Secret`` from
``request.headers`` (they compare against ``settings.internal_secret``
instead), so the finding is not exploitable today.  However, Caddy stripping
the header at the edge is the defense-in-depth control that ensures even a
future endpoint regression cannot be exploited externally.

Headers in scope (SPEC-SEC-AUDIT-2026-04 C1):
    X-Internal-Secret  — service-to-service shared secret
    X-Caller-Service   — IDENTITY-ASSERT caller identity
    X-Org-ID           — tenant scope injected on internal hops
    X-User-ID          — user identity injected by portal / knowledge-mcp

What this test does NOT check
------------------------------
* Tenant caddyfiles imported from ``/etc/caddy/tenants/*.caddyfile`` — those
  route LibreChat <-> knowledge-mcp on the internal Docker network, where
  these headers are legitimately present.  The import directive itself is
  detected and excluded from the assertion set.
* The syntax correctness of the Caddyfile — use ``caddy validate`` for that.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate the Caddyfile relative to this test file.
# Repo layout: tests/caddy/ -> ../../deploy/caddy/Caddyfile
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CADDYFILE = _REPO_ROOT / "deploy" / "caddy" / "Caddyfile"

# Headers that MUST be stripped in every public-facing reverse_proxy block.
_REQUIRED_STRIPS = {
    "X-Internal-Secret",
    "X-Caller-Service",
    "X-Org-ID",
    "X-User-ID",
}


def _extract_reverse_proxy_blocks(text: str) -> list[tuple[int, str]]:
    """Return (line_number, block_text) for every reverse_proxy directive.

    Each block is the ``reverse_proxy <upstream> { ... }`` span.
    Single-line ``reverse_proxy <upstream>`` (no braces) are also returned
    as single-line blocks — they have no header_up directives, which will
    cause the test to fail as intended.
    """
    blocks: list[tuple[int, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        if not stripped.startswith("reverse_proxy "):
            i += 1
            continue
        line_no = i + 1  # 1-based for error messages
        # Look-ahead: is there an opening brace on the same line or next?
        rest = stripped
        brace_depth = rest.count("{") - rest.count("}")
        block_lines = [rest]
        j = i + 1
        if brace_depth > 0:
            while j < len(lines) and brace_depth > 0:
                l = lines[j]
                brace_depth += l.count("{") - l.count("}")
                block_lines.append(l.strip())
                j += 1
        blocks.append((line_no, "\n".join(block_lines)))
        i = j if j > i + 1 else i + 1
    return blocks


def _block_strips_header(block: str, header: str) -> bool:
    """Return True if the block contains ``header_up -<header>``."""
    # Match both exact case and case-insensitive variants.
    pattern = re.compile(
        r"header_up\s+-" + re.escape(header),
        re.IGNORECASE,
    )
    return bool(pattern.search(block))


def _is_internal_only_block(context_before: str) -> bool:
    """Heuristic: block is preceded by an import or tenant comment."""
    return "import" in context_before.lower()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


def test_caddyfile_exists() -> None:
    assert _CADDYFILE.exists(), (
        f"Caddyfile not found at {_CADDYFILE}. "
        "Run this test from the repository root."
    )


def test_public_reverse_proxy_strips_internal_headers() -> None:
    """Every public-facing reverse_proxy block must strip all internal-trust headers."""
    text = _CADDYFILE.read_text(encoding="utf-8")
    blocks = _extract_reverse_proxy_blocks(text)

    assert blocks, "No reverse_proxy directives found — is the Caddyfile path correct?"

    failures: list[str] = []

    for line_no, block in blocks:
        # Skip single-line blocks that proxy to the Garage S3 website host
        # but only have a Host header_up — those also need the strip directives,
        # so we intentionally do NOT skip them here.

        missing = [
            h for h in sorted(_REQUIRED_STRIPS) if not _block_strips_header(block, h)
        ]
        if missing:
            preview = textwrap.shorten(block.replace("\n", " "), width=120)
            failures.append(
                f"  Line {line_no}: missing header_up strip(s) for {missing!r}\n"
                f"    Block: {preview}"
            )

    if failures:
        joined = "\n".join(failures)
        raise AssertionError(
            "SPEC-SEC-AUDIT-2026-04 C1 regression:\n"
            "The following reverse_proxy blocks do not strip all internal-trust headers.\n"
            "Add `header_up -<HeaderName>` inside each block listed below.\n\n"
            + joined
        )


def test_required_strips_comment_present() -> None:
    """The Caddyfile must contain the SPEC reference comment for auditability."""
    text = _CADDYFILE.read_text(encoding="utf-8")
    assert "SPEC-SEC-AUDIT-2026-04 C1" in text, (
        "Expected the SPEC-SEC-AUDIT-2026-04 C1 audit comment to be present "
        "in deploy/caddy/Caddyfile."
    )
