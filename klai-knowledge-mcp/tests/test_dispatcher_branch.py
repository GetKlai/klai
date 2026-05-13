"""SPEC-MCP-AUTH-001 Fase 3: dispatcher branch unit-tests.

Pure-unit: tests _looks_like_oauth_access_token without spinning up
FastMCP, Redis, or portal-api. The dispatcher's correctness is the
load-bearing safety net for the LibreChat regression — a bug here that
misroutes a klai_mcp_rt_<...> refresh-token to the OAuth pad would let
an attacker bypass the access-token expiry guard, and a bug that
misroutes a Zitadel JWT to the OAuth pad would silently break LibreChat.

Tests run without the module-level env-var imports (which would fail in
isolation). The function is imported via a lazy-import shim so the
module-load environment doesn't matter.
"""

from __future__ import annotations

import sys
from pathlib import Path

# dispatcher.py has zero external dependencies — no env-var preconditions,
# no FastMCP, no klai-libs imports. We can exercise it directly from tests.
sys.path.insert(0, str(Path(__file__).parent.parent))

from dispatcher import looks_like_oauth_access_token


def test_oauth_access_token_prefix_routes_to_oauth_pad() -> None:
    assert looks_like_oauth_access_token("Bearer klai_mcp_xyzabc123") is True


def test_oauth_refresh_token_prefix_does_not_route_to_oauth_pad() -> None:
    """Critical: refresh-tokens MUST NOT pass the OAuth dispatcher.

    Refresh tokens are valid only on portal-api /oauth/token. Misrouting
    them to knowledge-mcp's verify-pad would treat them as access-tokens —
    and the verify endpoint correctly rejects them, but the dispatcher
    layer is the first defense.
    """
    assert looks_like_oauth_access_token("Bearer klai_mcp_rt_xyzabc123") is False


def test_zitadel_jwt_falls_through_to_librechat_pad() -> None:
    """LibreChat-pad regression guard.

    LibreChat optionally forwards a Zitadel JWT in Authorization. The
    dispatcher must NOT treat that as an OAuth-token — it must fall
    through to the existing internal-secret pad.
    """
    jwt = "Bearer eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIn0.eyJzdWIiOiI"
    assert looks_like_oauth_access_token(jwt) is False


def test_no_authorization_header_falls_through() -> None:
    assert looks_like_oauth_access_token("") is False


def test_authorization_without_bearer_scheme_falls_through() -> None:
    assert looks_like_oauth_access_token("Basic abc==") is False
    assert looks_like_oauth_access_token("klai_mcp_xyz") is False  # no Bearer prefix


def test_bearer_case_insensitive() -> None:
    """RFC 6750 says the scheme is case-insensitive."""
    assert looks_like_oauth_access_token("bearer klai_mcp_abc") is True
    assert looks_like_oauth_access_token("BEARER klai_mcp_abc") is True
