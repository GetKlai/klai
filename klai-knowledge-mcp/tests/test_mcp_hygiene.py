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

from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"
DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "caddy" / "Caddyfile"


@pytest.fixture(scope="module")
def mcp_client() -> Iterator[TestClient]:
    """Start the SDK session manager once; MCP 2.0 managers cannot restart."""
    import main

    with TestClient(main.app) as client:
        yield client


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


def test_dns_rebinding_protection_actually_rejects_a_foreign_host(
    mcp_client: TestClient,
) -> None:
    """SPEC-MCP-AUTH-001 REQ-A6 — the settings are WIRED UP, not merely written down.

    The test above greps main.py for ``enable_dns_rebinding_protection=True``.
    That is a text check, and it stayed green through the mcp SDK v2 migration
    even though v2 moved ``transport_security`` off the server constructor onto
    ``streamable_http_app()`` — a settings object that is built and never passed
    satisfies it perfectly. So this one asks the running app instead.

    It drives ``main.app``, the app uvicorn actually serves, rather than the
    inner ``_mcp_app``: that way a regression in how the MCP app is mounted, or
    in middleware order, fails here too. Reaching the transport requires getting
    past ``_WWWAuthenticateMiddleware``, which only prefix-matches the bearer
    token, so a dummy ``klai_mcp_`` token is enough — the tools themselves still
    verify it against portal-api, and we never get that far.

    Both statuses are pinned deliberately. An allow-listed Host reaches the
    transport and fails there on protocol grounds (400, no session ID); a
    foreign Host is refused by the host gate (421). Asserting only ``!= 421``
    for the allowed case would also accept a 404 or a 500 from an app that no
    longer serves /mcp at all.
    """
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_test-token-never-verified",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    for allowed in ("mcp.getklai.com", "klai-knowledge-mcp:8080"):
        response = mcp_client.post("/mcp", headers={**headers, "Host": allowed}, json=body)
        assert response.status_code == 400, (
            f"allow-listed Host {allowed!r} should reach the MCP transport and fail "
            f"there on the missing session ID; got {response.status_code}. 421 means "
            "the host gate rejected it and production traffic through Caddy and "
            "LibreChat would both break."
        )

    for foreign in ("evil.example.com", "attacker.test"):
        response = mcp_client.post("/mcp", headers={**headers, "Host": foreign}, json=body)
        assert response.status_code == 421, (
            f"Host {foreign!r} should be refused with 421 Misdirected Request; got "
            f"{response.status_code}. DNS-rebinding protection is not reaching the app."
        )


def test_rejected_foreign_hosts_do_not_consume_mcp_sessions(mcp_client: TestClient) -> None:
    """A transport-security rejection must not consume process-lifetime session capacity."""
    import main

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_test-token-never-verified",
        "Host": "evil.example.com",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    responses = [mcp_client.post("/mcp", headers=headers, json=body) for _ in range(50)]

    assert {response.status_code for response in responses} == {421}
    assert len(session_manager._server_instances) == sessions_before


def test_rejected_foreign_origins_do_not_consume_mcp_sessions(mcp_client: TestClient) -> None:
    """The adjacent Origin rejection must happen before session allocation too."""
    import main

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_test-token-never-verified",
        "Host": "mcp.getklai.com",
        "Origin": "https://evil.example.com",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    response = mcp_client.post("/mcp", headers=headers, json=body)

    assert response.status_code == 403
    assert len(session_manager._server_instances) == sessions_before


@pytest.mark.parametrize(
    "origin",
    [
        "https://mcp.getklai.com",
        "https://chat.openai.com",
        "https://chatgpt.com",
        "https://claude.ai",
        "https://claude.com",
    ],
)
def test_supported_browser_origins_reach_mcp_transport(
    mcp_client: TestClient,
    origin: str,
) -> None:
    """Registered web MCP clients must pass transport security."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_test-token-never-verified",
        "Host": "mcp.getklai.com",
        "Origin": origin,
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    response = mcp_client.post("/mcp", headers=headers, json=body)

    assert response.status_code == 400, (
        f"supported Origin {origin!r} should reach the MCP transport and fail there on "
        f"the missing session ID; got {response.status_code}. 403 means the Origin gate "
        "rejected it."
    )


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
