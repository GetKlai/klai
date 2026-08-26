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
from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from klai_identity_assert.mcp_token_client import McpTokenVerifyResult
from starlette.testclient import TestClient
from starlette.types import Message, Scope

MAIN_PY = Path(__file__).resolve().parents[1] / "main.py"
DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
CADDYFILE = Path(__file__).resolve().parents[2] / "deploy" / "caddy" / "Caddyfile"

_VALID_INITIALIZE_PARAMS = (
    b'{"protocolVersion":"2025-06-18","capabilities":{},'
    b'"clientInfo":{"name":"klai-session-guard-test","version":"1"}}'
)
_SESSIONLESS_BODY_CORPUS = (
    (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        + _VALID_INITIALIZE_PARAMS
        + b"}",
        200,
    ),
    (b'{"params":' + _VALID_INITIALIZE_PARAMS + b"}", 400),
    (b'{"jsonrpc":"2.0","id":1,"params":' + _VALID_INITIALIZE_PARAMS + b"}", 400),
    (b'{"jsonrpc":"2.0","method":"initialize","params":' + _VALID_INITIALIZE_PARAMS + b"}", 400),
    (b'{"id":1,"method":"initialize","params":' + _VALID_INITIALIZE_PARAMS + b"}", 400),
    (
        b'{"jsonrpc":"1.0","id":1,"method":"initialize","params":'
        + _VALID_INITIALIZE_PARAMS
        + b"}",
        400,
    ),
    (
        b'{"jsonrpc":"2.0","id":{},"method":"initialize","params":'
        + _VALID_INITIALIZE_PARAMS
        + b"}",
        400,
    ),
    (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","method":"ping","params":'
        + _VALID_INITIALIZE_PARAMS
        + b"}",
        400,
    ),
    (b'{"jsonrpc":"2.0","id":1,"method":"ping"}', 400),
    (
        b'[{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        + _VALID_INITIALIZE_PARAMS
        + b"}]",
        400,
    ),
    (b"{}", 400),
    (b"{", 400),
)
_SESSIONLESS_BODY_CORPUS_IDS = (
    "valid-initialize",
    "methodless-valid-params",
    "methodless-jsonrpc-id",
    "notification-shaped-initialize",
    "missing-jsonrpc",
    "jsonrpc-1.0",
    "object-id",
    "duplicate-method-initialize-then-ping",
    "ping",
    "batch",
    "empty-object",
    "malformed-json",
)


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
    ("body", "expected_status"),
    _SESSIONLESS_BODY_CORPUS,
    ids=_SESSIONLESS_BODY_CORPUS_IDS,
)
def test_sessionless_guard_matches_the_sdk_envelope_contract_without_leaking_sessions(
    mcp_client: TestClient,
    body: bytes,
    expected_status: int,
) -> None:
    """Every rejected legacy sessionless envelope must leave session state unchanged."""
    import main

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_verified-corpus",
        "Host": "mcp.getklai.com",
    }
    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    with patch(
        "main._mcp_token_asserter.verify",
        new_callable=AsyncMock,
        return_value=McpTokenVerifyResult.allow(
            user_id="verified-user",
            org_id="verified-org",
            org_slug="verified-org",
            scopes=("mcp:knowledge",),
            resource_uri="https://mcp.getklai.com/mcp",
        ),
    ):
        response = mcp_client.post("/mcp", headers=headers, content=body)

    assert response.status_code == expected_status, response.text
    if response.status_code != 200:
        assert len(session_manager._server_instances) == sessions_before
        return

    session_headers = {
        **headers,
        "Mcp-Session-Id": response.headers["mcp-session-id"],
        "MCP-Protocol-Version": "2025-06-18",
    }
    deleted = mcp_client.delete("/mcp", headers=session_headers)
    assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_sessionless_disconnect_returns_a_response_without_allocating_a_session() -> None:
    """A client abort during the guard's body drain must not escape the ASGI app."""
    import main

    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"mcp.getklai.com"),
                (b"authorization", b"Bearer klai_mcp_disconnect"),
                (b"accept", b"application/json, text/event-stream"),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("mcp.getklai.com", 443),
        },
    )
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    await main.app(scope, receive, send)

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 400
    assert len(session_manager._server_instances) == sessions_before


@pytest.mark.asyncio
async def test_declared_oversized_sessionless_body_is_rejected_before_body_read() -> None:
    """The outer guard must preserve the SDK's header-only body-limit rejection."""
    import main

    declared_size = main.mcp.session_manager.max_request_body_size + 1
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"mcp.getklai.com"),
                (b"authorization", b"Bearer klai_mcp_oversized"),
                (b"accept", b"application/json, text/event-stream"),
                (b"content-type", b"application/json"),
                (b"content-length", str(declared_size).encode("ascii")),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("mcp.getklai.com", 443),
        },
    )
    sent: list[Message] = []
    receive_calls = 0

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        raise AssertionError("oversized declared body must not be read")

    async def send(message: Message) -> None:
        sent.append(message)

    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    await main.app(scope, receive, send)

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert receive_calls == 0
    assert len(session_manager._server_instances) == sessions_before


def test_sessionless_non_initialize_requests_do_not_consume_mcp_sessions(
    mcp_client: TestClient,
) -> None:
    """Only initialize may allocate a session for a request without a session ID."""
    import main

    common_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    request_paths = (
        {
            "Host": "mcp.getklai.com",
            "Authorization": "Bearer klai_mcp_test-token-never-verified",
        },
        {
            "Host": "klai-knowledge-mcp:8080",
            "X-Internal-Secret": "test-secret",
        },
    )
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    responses = [
        mcp_client.post("/mcp", headers={**common_headers, **path_headers}, json=body)
        for path_headers in request_paths
        for _ in range(100)
    ]

    assert {response.status_code for response in responses} == {400}
    assert len(session_manager._server_instances) == sessions_before


@pytest.mark.parametrize(
    "deny_reason",
    ("unknown_token", "portal_unreachable"),
    ids=("invalid-credential", "verifier-unavailable"),
)
def test_initialize_requires_verified_bearer_before_session_allocation(
    mcp_client: TestClient,
    deny_reason: str,
) -> None:
    """A syntactically valid bearer credential is not authenticated until verified."""
    import main

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_unverified-initialize",
        "Host": "mcp.getklai.com",
    }
    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    with patch(
        "main._mcp_token_asserter.verify",
        new_callable=AsyncMock,
        return_value=McpTokenVerifyResult.deny(deny_reason),
    ) as verify_mock:
        response = mcp_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "klai-auth-contract-test", "version": "1"},
                },
            },
        )

    assert response.status_code == 401
    assert "www-authenticate" in response.headers
    assert "mcp-session-id" not in response.headers
    assert len(session_manager._server_instances) == sessions_before
    assert verify_mock.await_args is not None
    assert verify_mock.await_args.kwargs["raw_token"] == "klai_mcp_unverified-initialize"


def test_initialize_requires_valid_internal_secret_before_session_allocation(
    mcp_client: TestClient,
) -> None:
    """Internal initialize authenticates the shared secret before reaching FastMCP."""
    import main

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "X-Internal-Secret": "wrong-internal-secret",
        "Host": "klai-knowledge-mcp:8080",
    }
    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    response = mcp_client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "klai-auth-contract-test", "version": "1"},
            },
        },
    )

    assert response.status_code == 401
    assert "mcp-session-id" not in response.headers
    assert len(session_manager._server_instances) == sessions_before


def test_sessionless_2026_discover_remains_available_without_allocating_a_session(
    mcp_client: TestClient,
) -> None:
    """The 2026 discovery handshake is stateless and must remain reachable."""
    import main

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_test-token-never-verified",
        "Host": "mcp.getklai.com",
        "MCP-Protocol-Version": "2026-07-28",
        "MCP-Method": "server/discover",
    }
    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    response = mcp_client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": {
                        "name": "klai-session-guard-test",
                        "version": "1",
                    },
                }
            },
        },
    )

    assert response.status_code == 200, response.text
    assert "2026-07-28" in response.json()["result"]["supportedVersions"]
    assert "mcp-session-id" not in response.headers
    assert len(session_manager._server_instances) == sessions_before


@pytest.mark.parametrize(
    "body",
    (
        b"{",
        b'{"jsonrpc":"2.0","id":1,"method":"initialize"}',
    ),
    ids=("malformed-json", "incomplete-initialize"),
)
def test_invalid_initialize_requests_do_not_consume_mcp_sessions(
    mcp_client: TestClient,
    body: bytes,
) -> None:
    """A method label alone is not a valid initialize request and cannot allocate."""
    import main

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_test-token-never-verified",
        "Host": "mcp.getklai.com",
    }
    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    response = mcp_client.post("/mcp", headers=headers, content=body)

    assert response.status_code == 400
    assert len(session_manager._server_instances) == sessions_before


@pytest.mark.parametrize(
    "accept",
    ("application/json", "text/event-stream", "text/html"),
    ids=("missing-sse", "missing-json", "unsupported"),
)
def test_initialize_with_invalid_accept_does_not_allocate_a_session(
    mcp_client: TestClient,
    accept: str,
) -> None:
    """Transport negotiation must fail before a stateful session is registered."""
    import main

    headers = {
        "Accept": accept,
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_test-token-never-verified",
        "Host": "mcp.getklai.com",
    }
    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    response = mcp_client.post(
        "/mcp",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "klai-session-guard-test", "version": "1"},
            },
        },
    )

    assert response.status_code == 406
    assert "mcp-session-id" not in response.headers
    assert len(session_manager._server_instances) == sessions_before


def test_sessionless_get_does_not_consume_an_mcp_session(mcp_client: TestClient) -> None:
    """Standalone SSE is valid only after initialize has established a session."""
    import main

    headers = {
        "Accept": "text/event-stream",
        "Authorization": "Bearer klai_mcp_test-token-never-verified",
        "Host": "mcp.getklai.com",
    }
    session_manager = main.mcp.session_manager
    sessions_before = len(session_manager._server_instances)

    response = mcp_client.get("/mcp", headers=headers)

    assert response.status_code == 400
    assert len(session_manager._server_instances) == sessions_before


@pytest.mark.parametrize(
    ("host", "auth_headers"),
    (
        (
            "mcp.getklai.com",
            {"Authorization": "Bearer klai_mcp_test-token-never-verified"},
        ),
        (
            "klai-knowledge-mcp:8080",
            {"X-Internal-Secret": "test-secret"},
        ),
    ),
    ids=("public", "librechat"),
)
def test_legitimate_mcp_handshake_survives_sessionless_request_guard(
    mcp_client: TestClient,
    host: str,
    auth_headers: dict[str, str],
) -> None:
    """Body inspection must preserve initialize, session use, and termination end to end."""
    common_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Host": host,
        **auth_headers,
    }
    verifier = (
        patch(
            "main._mcp_token_asserter.verify",
            new_callable=AsyncMock,
            return_value=McpTokenVerifyResult.allow(
                user_id="verified-user",
                org_id="verified-org",
                org_slug="verified-org",
                scopes=("mcp:knowledge",),
                resource_uri="https://mcp.getklai.com/mcp",
            ),
        )
        if "Authorization" in auth_headers
        else nullcontext()
    )
    with verifier:
        initialize = mcp_client.post(
            "/mcp",
            headers=common_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "klai-session-guard-test", "version": "1"},
                },
            },
        )
    assert initialize.status_code == 200
    session_id = initialize.headers["mcp-session-id"]
    session_headers = {
        **common_headers,
        "Mcp-Session-Id": session_id,
        "MCP-Protocol-Version": "2025-06-18",
    }

    initialized = mcp_client.post(
        "/mcp",
        headers=session_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    tools_list = mcp_client.post(
        "/mcp",
        headers=session_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    deleted = mcp_client.delete("/mcp", headers=session_headers)

    assert initialized.status_code == 202
    assert tools_list.status_code == 200
    assert deleted.status_code == 200


def test_session_does_not_replace_tool_level_bearer_revalidation(
    mcp_client: TestClient,
) -> None:
    """Creating a session does not pin an allow result for later tool calls."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Authorization": "Bearer klai_mcp_revalidation-contract",
        "Host": "mcp.getklai.com",
    }
    allow = McpTokenVerifyResult.allow(
        user_id="verified-user",
        org_id="verified-org",
        org_slug="verified-org",
        scopes=("mcp:knowledge",),
        resource_uri="https://mcp.getklai.com/mcp",
    )

    with patch(
        "main._mcp_token_asserter.verify",
        new_callable=AsyncMock,
        side_effect=(allow, McpTokenVerifyResult.deny("token_revoked")),
    ) as verify_mock:
        initialize = mcp_client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "klai-auth-contract-test", "version": "1"},
                },
            },
        )
        assert initialize.status_code == 200
        session_headers = {
            **headers,
            "Mcp-Session-Id": initialize.headers["mcp-session-id"],
            "MCP-Protocol-Version": "2025-06-18",
        }

        initialized = mcp_client.post(
            "/mcp",
            headers=session_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        tool_call = mcp_client.post(
            "/mcp",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_docs_kbs", "arguments": {}},
            },
        )
        mcp_client.delete("/mcp", headers=session_headers)

    assert initialized.status_code == 202
    assert tool_call.status_code == 200
    assert '"isError":true' in tool_call.text
    assert verify_mock.await_count == 2


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
