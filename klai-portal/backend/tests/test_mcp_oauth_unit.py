"""Pure-unit tests for SPEC-MCP-AUTH-001 service-layer logic.

No DB, no Redis, no FastAPI. Tests the deterministic helpers in
``app.services.mcp_oauth`` that don't touch external state:

- PKCE S256 round-trip
- Token-prefix detection
- Redirect-URI allowlist (REQ-20 + REQ-13a)
- Token format generation

Integration tests for the full OAuth flow (DCR → consent → token) live
in ``test_mcp_oauth_integration.py`` (next implementation cycle, requires
TestClient + DB fixtures).
"""

from __future__ import annotations

import base64
import hashlib

from app.services.mcp_oauth import (
    ACCESS_TOKEN_PREFIX,
    REFRESH_TOKEN_PREFIX,
    is_redirect_uri_allowed,
    looks_like_access_token,
    verify_pkce_s256,
)

# ─── PKCE ────────────────────────────────────────────────────────────────


def _make_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636 § 4.2."""
    verifier = "u8gT5xQH2vZjGgJgGqDF7q7HnPcSJzN3RrUq8Vx2lYE"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def test_pkce_s256_round_trip() -> None:
    verifier, challenge = _make_pkce_pair()
    assert verify_pkce_s256(verifier, challenge) is True


def test_pkce_s256_tampered_verifier_rejected() -> None:
    _, challenge = _make_pkce_pair()
    assert verify_pkce_s256("wrong-verifier", challenge) is False


def test_pkce_s256_tampered_challenge_rejected() -> None:
    verifier, _ = _make_pkce_pair()
    assert verify_pkce_s256(verifier, "tampered-challenge") is False


def test_pkce_s256_empty_inputs_rejected() -> None:
    assert verify_pkce_s256("", "") is False
    assert verify_pkce_s256("verifier", "") is False
    assert verify_pkce_s256("", "challenge") is False


# ─── Token-prefix dispatch ───────────────────────────────────────────────


def test_looks_like_access_token_true_for_access_prefix() -> None:
    assert looks_like_access_token(f"{ACCESS_TOKEN_PREFIX}abc123") is True


def test_looks_like_access_token_false_for_refresh_prefix() -> None:
    """Critical: the dispatcher must NEVER route refresh-tokens to the OAuth pad.

    Refresh tokens are only valid on /oauth/token in portal — using one as
    a bearer credential on knowledge-mcp would let an attacker who stole a
    refresh token (much longer-lived than access tokens) bypass the access-
    token expiry guard.
    """
    assert looks_like_access_token(f"{REFRESH_TOKEN_PREFIX}xyz789") is False


def test_looks_like_access_token_false_for_zitadel_jwt() -> None:
    """JWT bearers from LibreChat-pad must fall through to the LibreChat dispatcher.

    A naive prefix check that allowed any Bearer would route Zitadel JWTs
    to the OAuth pad and break the LibreChat regression.
    """
    jwt = "eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIn0.eyJzdWIiOiI..."
    assert looks_like_access_token(jwt) is False


def test_looks_like_access_token_false_for_empty() -> None:
    assert looks_like_access_token("") is False


def test_looks_like_access_token_false_for_arbitrary_string() -> None:
    assert looks_like_access_token("not_a_token") is False


# ─── Redirect URI allowlist (REQ-20 + REQ-13a / A10) ─────────────────────


def test_redirect_uri_native_localhost_http_accepted() -> None:
    """Native MCP clients bind to localhost. HTTP allowed, any port."""
    assert is_redirect_uri_allowed("http://localhost:54321/callback", "native") is True
    assert is_redirect_uri_allowed("http://127.0.0.1:8000/cb", "native") is True


def test_redirect_uri_native_https_rejected() -> None:
    """Native + HTTPS-redirect = phishing-pattern. Reject."""
    assert is_redirect_uri_allowed("https://localhost:54321/cb", "native") is False


def test_redirect_uri_native_external_host_rejected() -> None:
    """Native + non-localhost host = phishing. Reject."""
    assert is_redirect_uri_allowed("http://attacker.example.com/cb", "native") is False
    assert is_redirect_uri_allowed("http://192.168.1.10/cb", "native") is False


def test_redirect_uri_web_https_allowlisted_host_accepted() -> None:
    """Web MCP clients (ChatGPT, Claude.ai) on hardcoded HTTPS hosts."""
    assert is_redirect_uri_allowed("https://chat.openai.com/oauth/callback", "web") is True
    assert is_redirect_uri_allowed("https://chatgpt.com/cb", "web") is True
    assert is_redirect_uri_allowed("https://claude.ai/api/oauth/cb", "web") is True


def test_redirect_uri_web_http_rejected() -> None:
    """Web + HTTP = MITM-vulnerable. Reject."""
    assert is_redirect_uri_allowed("http://chat.openai.com/cb", "web") is False


def test_redirect_uri_web_localhost_rejected() -> None:
    """Web + localhost = native masquerading as web. Reject (REQ-13a)."""
    assert is_redirect_uri_allowed("https://localhost/cb", "web") is False


def test_redirect_uri_subdomain_wildcard_attack_rejected() -> None:
    """attacker.openai.com.evil.com must not match *.openai.com.

    Hardcoded list (no wildcards) is the structural defense — see
    research.md §11.
    """
    assert is_redirect_uri_allowed("https://chat.openai.com.evil.com/cb", "web") is False


def test_redirect_uri_unknown_application_type_rejected() -> None:
    """Caller must specify native or web — no default."""
    assert is_redirect_uri_allowed("http://localhost/cb", "service") is False
    assert is_redirect_uri_allowed("http://localhost/cb", "") is False


def test_redirect_uri_malformed_rejected() -> None:
    """Garbage input rejected without raising."""
    assert is_redirect_uri_allowed("not-a-url", "native") is False
    assert is_redirect_uri_allowed("file:///etc/passwd", "native") is False


def test_redirect_uri_uppercase_host_normalized() -> None:
    """Hostnames are case-insensitive per RFC 3986."""
    assert is_redirect_uri_allowed("http://LOCALHOST:54321/cb", "native") is True
    assert is_redirect_uri_allowed("https://CHAT.OPENAI.COM/cb", "web") is True
