"""HY-45 — FastMCP DNS-rebinding annotation + Caddyfile reviewer-signal.

SPEC-SEC-HYGIENE-001 REQ-45.1, REQ-45.2, REQ-45.3.

The MCP service is currently safe because Caddy has NO upstream route to it
(stdio / Docker-internal only). If a future Caddy config adds an HTTP upstream,
``enable_dns_rebinding_protection`` MUST be flipped to True. Without an in-code
annotation and a matching Caddyfile note the reviewer of that future change has
nothing to trip them up.

This test is grep-shaped on purpose — the SPEC explicitly chose docs-only over
a runtime fix (the MCP is not internet-reachable, so DNS-rebinding is a future
threat, not a present one).
"""

from __future__ import annotations

import re
from pathlib import Path

MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"
CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "caddy" / "Caddyfile"


def test_dns_rebinding_flag_carries_mx_warn_and_reason() -> None:
    """REQ-45.1 / REQ-45.3 — annotation block above the False flag.

    The reviewer who flips the flag back to True (or who adds a Caddy upstream)
    must encounter an MX:WARN explaining why the flag matters and which SPEC
    governs the change.
    """
    text = MAIN_PY.read_text(encoding="utf-8")
    lines = text.splitlines()

    flag_idx = next(
        (i for i, line in enumerate(lines) if "enable_dns_rebinding_protection=False" in line),
        None,
    )
    assert flag_idx is not None, (
        "expected enable_dns_rebinding_protection=False somewhere in main.py"
    )

    # Look back at most 16 lines for the MX:WARN block. Annotations on
    # security-relevant flags often span 10-12 lines because the @MX:REASON
    # carries WHY context (safe-today + when-to-flip + reviewer signal). The
    # window is bounded so a stray @MX:WARN 50 lines away does not satisfy
    # the test, but legitimate multi-line context blocks pass.
    window = "\n".join(lines[max(0, flag_idx - 16) : flag_idx])
    assert "@MX:WARN" in window, (
        "expected @MX:WARN annotation on a preceding comment line near the "
        f"enable_dns_rebinding_protection flag (line {flag_idx + 1})"
    )
    assert "@MX:REASON" in window, "expected @MX:REASON sub-line in the same block"
    # Reason must reference both the safe-today rationale and the SPEC ID
    # so a reviewer can trace it back without grep-archaeology.
    reason_text = window.lower()
    assert "not internet-reachable" in reason_text or "not public" in reason_text, (
        "@MX:REASON must explain WHY the flag is currently safe (MCP not internet-reachable)"
    )
    assert "spec-sec-hygiene-001" in reason_text, "@MX:REASON must reference SPEC-SEC-HYGIENE-001"
    assert "req-45" in reason_text, "@MX:REASON must reference REQ-45"


def test_caddyfile_lists_knowledge_mcp_as_not_internet_reachable() -> None:
    """REQ-45.2 — reviewer signal in Caddy config.

    Adding a future HTTP upstream for klai-knowledge-mcp without first removing
    this comment should be obvious in code review.
    """
    assert CADDYFILE.exists(), f"expected Caddyfile at {CADDYFILE}"
    text = CADDYFILE.read_text(encoding="utf-8")

    # The marker comment must explicitly name the service AND state that it is
    # not internet-reachable (or equivalent — "internal Docker network only" /
    # "Docker-internal", etc.). A bare service name without context is too easy
    # to delete by accident.
    pattern = re.compile(
        r"#[^\n]*klai-knowledge-mcp[^\n]*",
        flags=re.IGNORECASE,
    )
    matches = [m.group(0) for m in pattern.finditer(text)]
    assert matches, "expected a Caddyfile comment naming klai-knowledge-mcp"

    joined = "\n".join(matches).lower()
    assert any(
        phrase in joined
        for phrase in ("not internet-reachable", "not public", "docker-internal", "internal docker")
    ), (
        "expected the Caddyfile comment to explicitly mark klai-knowledge-mcp "
        "as not internet-reachable / Docker-internal — found: " + " | ".join(matches)
    )
