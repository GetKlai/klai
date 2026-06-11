"""Unit tests for :mod:`app.services.request_ip`.

Focused on the SPEC-SEC-SESSION-001 binding-subnet contract — including
the IPv4-mapped IPv6 edge case that research.md §7 explicitly called
out as a test-suite gap.

A naive implementation that just trusts ``ipaddress.ip_address(raw).version``
would route ``::ffff:1.2.3.4`` through the IPv6 ``/48`` branch and emit
``::`` as the network address. That string would then match every other
IPv4-mapped IPv6 caller in the world, making the cookie binding effectively
a no-op for any dual-stack proxy that reports v4 clients as v4-mapped.
``resolve_caller_ip_subnet`` therefore unwraps the embedded v4 first.
"""

from __future__ import annotations

from helpers import make_request

from app.services.request_ip import (
    resolve_caller_ip,
    resolve_caller_ip_subnet,
)

# ---------------------------------------------------------------------------
# resolve_caller_ip — uvicorn-validated client + fallbacks
# ---------------------------------------------------------------------------


def test_resolve_caller_ip_returns_proxy_validated_client_host() -> None:
    """Uvicorn rewrites ``request.client.host`` from XFF only for trusted peers."""
    request = make_request(client=("203.0.113.10", 12345))
    assert resolve_caller_ip(request) == "203.0.113.10"


def test_resolve_caller_ip_returns_direct_client_host_without_xff() -> None:
    request = make_request(client=("198.51.100.42", 12345))
    assert resolve_caller_ip(request) == "198.51.100.42"


def test_resolve_caller_ip_returns_unknown_on_synthetic_scope() -> None:
    """No XFF and no client → sentinel ``unknown``."""
    request = make_request(client=None)
    assert resolve_caller_ip(request) == "unknown"


def test_resolve_caller_ip_ignores_raw_xff_header_from_untrusted_peer() -> None:
    """Application code must not trust raw XFF; uvicorn owns that boundary."""
    request = make_request(
        headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8, 203.0.113.10"},
        client=("172.18.0.99", 12345),
    )
    assert resolve_caller_ip(request) == "172.18.0.99"


# ---------------------------------------------------------------------------
# resolve_caller_ip_subnet — happy paths
# ---------------------------------------------------------------------------


def test_subnet_ipv4_uses_slash_24() -> None:
    """198.51.100.214 → 198.51.100.0 (/24 network address)."""
    request = make_request(client=("198.51.100.214", 12345))
    assert resolve_caller_ip_subnet(request) == "198.51.100.0"


def test_subnet_ipv6_uses_slash_48() -> None:
    """2001:db8:1234:5678::1 → 2001:db8:1234:: (/48 network address)."""
    request = make_request(client=("2001:db8:1234:5678::1", 12345))
    assert resolve_caller_ip_subnet(request) == "2001:db8:1234::"


def test_subnet_returns_unknown_for_synthetic_scope() -> None:
    request = make_request(client=None)
    assert resolve_caller_ip_subnet(request) == "unknown"


def test_subnet_returns_unknown_for_unparseable_ip() -> None:
    """Garbage in client host → sentinel rather than raising."""
    request = make_request(client=("not-an-ip", 12345))
    assert resolve_caller_ip_subnet(request) == "unknown"


# ---------------------------------------------------------------------------
# IPv4-mapped IPv6 — research §7 open question 3
# ---------------------------------------------------------------------------


def test_subnet_unwraps_ipv4_mapped_ipv6_to_v4_slash_24() -> None:
    """``::ffff:198.51.100.10`` SHALL bind to ``198.51.100.0``, not ``::``.

    Without the unwrap step, every IPv4-mapped caller in the world
    resolves to the same ``::`` /48 — the binding becomes a no-op for
    every dual-stack proxy that reports v4 clients as v4-mapped IPv6.
    """
    request = make_request(client=("::ffff:198.51.100.10", 12345))
    assert resolve_caller_ip_subnet(request) == "198.51.100.0"


def test_subnet_unwrapped_ipv4_mapped_matches_native_ipv4() -> None:
    """An IPv4-mapped binding MUST equal the native-IPv4 binding for the
    same address. This is the property that makes the unwrap correct:
    a stolen cookie issued from native v4 should still validate when
    consumed via a dual-stack proxy that reports the same client as
    v4-mapped, and vice versa."""
    native = make_request(client=("198.51.100.10", 12345))
    mapped = make_request(client=("::ffff:198.51.100.10", 12345))
    assert resolve_caller_ip_subnet(native) == resolve_caller_ip_subnet(mapped)


def test_subnet_native_ipv6_is_not_unwrapped() -> None:
    """Native IPv6 (no v4-mapped prefix) keeps the /48 boundary.

    Defends against a refactor that over-eagerly applies ipv4_mapped
    detection to non-mapped IPv6 addresses.
    """
    request = make_request(client=("2001:db8:cafe:babe::1", 12345))
    assert resolve_caller_ip_subnet(request) == "2001:db8:cafe::"


def test_subnet_ipv4_mapped_carrier_handoff_inside_same_24() -> None:
    """Two v4-mapped IPv6 addresses inside the same v4 /24 must produce
    the same subnet — same property as the IPv4 mobile-carrier handoff
    test in ``test_idp_pending_binding``."""
    a = make_request(client=("::ffff:198.51.100.10", 12345))
    b = make_request(client=("::ffff:198.51.100.214", 12345))
    assert resolve_caller_ip_subnet(a) == resolve_caller_ip_subnet(b) == "198.51.100.0"


def test_subnet_ipv4_mapped_different_24_produces_different_subnet() -> None:
    a = make_request(client=("::ffff:198.51.100.10", 12345))
    b = make_request(client=("::ffff:203.0.113.10", 12345))
    assert resolve_caller_ip_subnet(a) != resolve_caller_ip_subnet(b)
