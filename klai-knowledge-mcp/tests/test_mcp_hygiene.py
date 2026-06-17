"""HY-45 + SPEC-MCP-AUTH-001 — FastMCP DNS-rebinding annotation + Caddy upstream.

SPEC-SEC-HYGIENE-001 REQ-45.1/45.2/45.3 originally guarded the
"klai-knowledge-mcp is NOT internet-reachable" invariant. SPEC-MCP-AUTH-001
explicitly LIFTS that invariant: as of the OAuth surface rollout, the MCP
IS internet-reachable via mcp.${DOMAIN} and DNS-rebinding-protection has
been flipped ON.

This test now enforces the new state:
- ``enable_dns_rebinding_protection=True`` carries @MX:ANCHOR
- Caddyfile has an ``mcp.${DOMAIN}`` upstream block routing to klai-knowledge-mcp
"""

from __future__ import annotations

from pathlib import Path

MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"
DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "caddy" / "Caddyfile"


def test_dns_rebinding_protection_is_enabled_with_anchor() -> None:
    """SPEC-MCP-AUTH-001 REQ-A6 — protection ON + @MX:ANCHOR with reasoning."""
    text = MAIN_PY.read_text(encoding="utf-8")
    lines = text.splitlines()

    flag_idx = next(
        (i for i, line in enumerate(lines) if "enable_dns_rebinding_protection=True" in line),
        None,
    )
    assert flag_idx is not None, (
        "expected enable_dns_rebinding_protection=True somewhere in main.py"
    )

    window = "\n".join(lines[max(0, flag_idx - 16) : flag_idx])
    assert "@MX:ANCHOR" in window or "@MX:WARN" in window, (
        "expected @MX:ANCHOR or @MX:WARN annotation on a preceding comment line near the "
        f"enable_dns_rebinding_protection flag (line {flag_idx + 1})"
    )
    assert "@MX:REASON" in window, "expected @MX:REASON sub-line in the same block"
    reason_text = window.lower()
    assert "dns-rebinding" in reason_text or "rebinding" in reason_text, (
        "@MX:REASON must explain WHY DNS-rebinding protection matters"
    )
    assert "spec-mcp-auth-001" in reason_text, "@MX:REASON must reference SPEC-MCP-AUTH-001"


def test_caddyfile_routes_mcp_subdomain_to_knowledge_mcp() -> None:
    """SPEC-MCP-AUTH-001 Fase 5 — Caddy upstream block for mcp.${DOMAIN}."""
    assert CADDYFILE.exists(), f"expected Caddyfile at {CADDYFILE}"
    text = CADDYFILE.read_text(encoding="utf-8")
    assert "mcp.{$DOMAIN}" in text, (
        "expected a Caddy host matcher for mcp.{$DOMAIN} (SPEC-MCP-AUTH-001 Fase 5)"
    )
    assert "reverse_proxy klai-knowledge-mcp" in text, (
        "expected reverse_proxy to klai-knowledge-mcp in the mcp.${DOMAIN} block"
    )


def test_dockerfile_copies_local_runtime_modules_imported_by_main() -> None:
    """Local modules imported by main.py must be present in the runtime image."""
    main_text = MAIN_PY.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "from shield_compliance import" in main_text
    assert "klai-knowledge-mcp/shield_compliance.py" in dockerfile
