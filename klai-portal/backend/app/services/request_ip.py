"""Caller-IP resolution helpers for portal-api.

The right-most entry of ``X-Forwarded-For`` is the IP that Caddy (the
immediate trusted upstream) saw on the wire. Attacker-supplied entries that
clients prepend to ``XFF`` end up to the LEFT of that entry, so they are
ignored by ``resolve_caller_ip``.

Two consumers share the helper:

- ``app.api.internal``: rate-limit key + audit row (``SPEC-SEC-005 REQ-1.6``).
- ``app.api.auth`` / ``app.api.signup``: ``klai_idp_pending`` cookie binding
  to a ``/24`` IPv4 (or ``/48`` IPv6) subnet
  (``SPEC-SEC-SESSION-001 REQ-2.3``).

Pulled out of ``app.api.internal`` once the third callsite landed; before
that it lived as a private ``_resolve_caller_ip`` next to its only caller.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

# IPv4 /24 and IPv6 /48 are the SPEC-SEC-SESSION-001 binding boundaries.
# /24 covers most carrier-NAT pools without crossing a major site boundary;
# /48 is the typical IPv6 site-prefix length for residential and mobile ISPs.
_IPV4_BINDING_PREFIX = 24
_IPV6_BINDING_PREFIX = 48

# Sentinel returned when the caller IP cannot be resolved or parsed. Stable
# string so callers can compare ``resolve_caller_ip_subnet(req) == "unknown"``.
_UNKNOWN = "unknown"


def resolve_caller_ip(request: Request) -> str:
    """Return the best-effort caller IP for the request.

    Priority order:
    1. Right-most entry of ``X-Forwarded-For`` from the immediate trusted
       upstream (Caddy). Attacker-supplied left-side entries are dropped.
    2. ``request.client.host``.
    3. The literal string ``"unknown"`` (e.g. synthetic ASGI scope).
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    if request.client and request.client.host:
        return request.client.host
    return _UNKNOWN


def resolve_caller_ip_subnet(request: Request) -> str:
    """Return the binding-subnet network address for the caller.

    IPv4 → ``/24`` network address. IPv6 → ``/48`` network address. Returns
    ``"unknown"`` when the caller IP is missing or cannot be parsed by
    :mod:`ipaddress`.

    Used by the ``klai_idp_pending`` Fernet cookie binding so a stolen cookie
    replayed from a different network is rejected, while a mobile user
    switching cells inside the same carrier prefix is not (the new IP is
    almost always inside the same ``/24``/``/48``). See SPEC-SEC-SESSION-001
    research §3.2 for the threat-model rationale.
    """
    raw = resolve_caller_ip(request)
    if raw == _UNKNOWN:
        return _UNKNOWN
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return _UNKNOWN
    prefix = _IPV4_BINDING_PREFIX if addr.version == 4 else _IPV6_BINDING_PREFIX
    network = ipaddress.ip_network(f"{raw}/{prefix}", strict=False)
    return str(network.network_address)
