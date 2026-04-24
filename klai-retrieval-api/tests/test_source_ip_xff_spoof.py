"""SPEC-SEC-WEBHOOK-001 REQ-1.5 / REQ-5.6 — XFF-spoof bucket-identity regression.

Before this SPEC, `_source_ip` read the `X-Forwarded-For` header directly from
the request. Any klai-net peer (portal-api, litellm, etc.) could forge an XFF
value and either bypass the 600 rpm rate-limit ceiling (by rotating the forged
IP per request) or collapse all service-to-service traffic into the caller's
bucket, denying legitimate requests.

After this SPEC:
- retrieval-api's uvicorn runs with `--proxy-headers --forwarded-allow-ips=127.0.0.1`
  so `request.client.host` always reflects the TCP peer (per Dockerfile change).
- `_source_ip` uses `request.client.host` directly and NEVER reads the raw
  `X-Forwarded-For` header.

This test verifies the second half (code-level): even if a caller stuffs a
forged `X-Forwarded-For` into the request, the derived rate-limit key is based
on the TCP peer, not on the forged value.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request as StarletteRequest

from retrieval_api.middleware.auth import AuthContext, _rate_limit_key, _source_ip


def _make_request(tcp_peer: str | None, xff: str | None) -> StarletteRequest:
    """Build a minimal Starlette Request with a controllable TCP peer and XFF header.

    We bypass the real Starlette scope construction because `_source_ip` only
    reads `request.client.host` and `request.headers`; a MagicMock is
    sufficient and much cheaper to construct than a real ASGI scope.
    """
    req = MagicMock(spec=StarletteRequest)
    req.client = MagicMock(host=tcp_peer) if tcp_peer is not None else None
    # Starlette's request.headers is case-insensitive; use a dict for simplicity
    # since _source_ip only does `.get("x-forwarded-for")`.
    req.headers = {}
    if xff is not None:
        req.headers = {"x-forwarded-for": xff}
    return req


class TestSourceIpIgnoresForgedXFF:
    """REQ-5.6 acceptance: a forged X-Forwarded-For from a klai-net peer must
    NOT influence the rate-limit bucket identity."""

    def test_source_ip_uses_tcp_peer_when_no_xff(self) -> None:
        req = _make_request(tcp_peer="172.18.0.42", xff=None)
        assert _source_ip(req) == "172.18.0.42"

    def test_source_ip_ignores_forged_xff_header(self) -> None:
        """Caller from 172.18.0.42 forges `X-Forwarded-For: 1.2.3.4` to try to
        shift into a different (or fresh) rate-limit bucket. _source_ip MUST
        return the TCP peer, not the forged value."""
        req = _make_request(tcp_peer="172.18.0.42", xff="1.2.3.4")
        assert _source_ip(req) == "172.18.0.42"
        assert _source_ip(req) != "1.2.3.4", "Forged XFF must not appear in source IP"

    def test_source_ip_ignores_multi_hop_forged_xff(self) -> None:
        """Attacker sends multi-hop XFF like `1.2.3.4, 5.6.7.8, evil.example`
        attempting to impersonate a CDN-forwarded request. TCP peer still wins."""
        req = _make_request(
            tcp_peer="172.18.0.99",
            xff="1.2.3.4, 5.6.7.8, malicious.spoof",
        )
        assert _source_ip(req) == "172.18.0.99"

    def test_source_ip_falls_back_to_unknown_when_no_client(self) -> None:
        """Defense-in-depth: even when Starlette gives us no client info (shouldn't
        happen on klai-net but surfaced in test envs), we return a stable sentinel
        rather than leaking None or raising."""
        req = _make_request(tcp_peer=None, xff=None)
        assert _source_ip(req) == "unknown"

    def test_source_ip_falls_back_to_unknown_even_with_forged_xff(self) -> None:
        """Critical: if there's no TCP peer AND the attacker sends XFF, we MUST
        NOT fall back to the header — that was the old behaviour and it's the
        exact XFF-spoof primitive this SPEC closes."""
        req = _make_request(tcp_peer=None, xff="1.2.3.4")
        assert _source_ip(req) == "unknown"
        assert _source_ip(req) != "1.2.3.4"


class TestRateLimitKeyDerivationFromSourceIp:
    """Verify _rate_limit_key composes correctly with the TCP-peer _source_ip,
    producing stable bucket identity per klai-net peer."""

    def test_internal_bucket_key_is_tcp_peer_for_internal_auth(self) -> None:
        req = _make_request(tcp_peer="172.18.0.42", xff="1.2.3.4")
        auth = AuthContext(method="internal", sub=None, resourceowner=None, role=None)
        assert _rate_limit_key(auth, req) == "retrieval:rl:internal:172.18.0.42"

    def test_internal_bucket_ignores_spoofed_xff(self) -> None:
        """Two requests from the same TCP peer with different forged XFF values
        MUST map to the same bucket — otherwise the rate-limit is bypassable."""
        auth = AuthContext(method="internal", sub=None, resourceowner=None, role=None)
        req1 = _make_request(tcp_peer="172.18.0.42", xff="1.2.3.4")
        req2 = _make_request(tcp_peer="172.18.0.42", xff="evil-spoof-attempt")
        assert _rate_limit_key(auth, req1) == _rate_limit_key(auth, req2)

    def test_jwt_bucket_uses_hashed_sub_not_ip(self) -> None:
        """JWT-authenticated requests use a hashed sub for the bucket, independent
        of both TCP peer and XFF. Regression check: the JWT path is unchanged by
        this SPEC."""
        auth = AuthContext(method="jwt", sub="user-123", resourceowner=None, role=None)
        req = _make_request(tcp_peer="172.18.0.42", xff="anything")
        key = _rate_limit_key(auth, req)
        assert key.startswith("retrieval:rl:jwt:")
        assert "172.18.0.42" not in key
        assert "anything" not in key


@pytest.mark.parametrize(
    "tcp_peer,xff,expected",
    [
        ("10.0.0.1", "1.2.3.4", "10.0.0.1"),
        ("192.168.1.5", "10.0.0.1, 10.0.0.2", "192.168.1.5"),
        ("172.18.0.7", "", "172.18.0.7"),  # empty XFF never promoted
        ("172.18.0.7", "   ", "172.18.0.7"),  # whitespace XFF never promoted
    ],
)
def test_source_ip_matrix(tcp_peer: str, xff: str, expected: str) -> None:
    """Parametrised sweep of the primary invariant: TCP peer always wins."""
    req = _make_request(tcp_peer=tcp_peer, xff=xff)
    assert _source_ip(req) == expected
