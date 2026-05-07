"""Dispatcher branch primitives — SPEC-MCP-AUTH-001 REQ-15.

Pure logic for routing incoming requests between the OAuth-token pad and
the existing LibreChat internal-secret pad. Kept in a separate module
(no FastMCP / klai-libs imports) so unit-tests can exercise the dispatcher
without spinning up the full MCP stack.

main.py imports from here; tests/test_dispatcher_branch.py also imports
from here directly without triggering main.py's heavy module-load
side-effects.
"""

from __future__ import annotations

# Access-token prefix — must match ``app.services.mcp_oauth.ACCESS_TOKEN_PREFIX``
# in the portal-api. Cross-service contract.
OAUTH_ACCESS_PREFIX = "klai_mcp_"
# Refresh-token prefix — explicitly excluded from the OAuth-pad branch.
# Refresh-tokens are never valid bearer credentials on knowledge-mcp; they
# only work on /oauth/token in portal-api.
OAUTH_REFRESH_PREFIX = "klai_mcp_rt_"


def looks_like_oauth_access_token(authorization: str) -> bool:
    """True iff the Authorization header carries a klai_mcp_<...> access token.

    Returns False for:
    - missing/empty header
    - non-Bearer schemes (Basic, Digest)
    - Zitadel JWT bearers (LibreChat-pad optional credential)
    - klai_mcp_rt_<...> refresh-tokens (security: refresh != access)
    - any other prefix

    Bearer scheme matching is case-insensitive per RFC 6750 § 2.1.
    """
    if not authorization.lower().startswith("bearer "):
        return False
    token = authorization.split(" ", 1)[1].strip()
    if token.startswith(OAUTH_REFRESH_PREFIX):
        return False
    return token.startswith(OAUTH_ACCESS_PREFIX)
