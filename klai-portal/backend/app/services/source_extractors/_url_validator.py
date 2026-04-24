"""SSRF guard + canonicalisation for user-supplied URLs.

SPEC-KB-SOURCES-001 D6 + R2.1 + R5.3. Mirrors the pattern in
klai-knowledge-ingest/knowledge_ingest/utils/url_validator.py but here on
portal-api. A follow-up refactor can lift this into klai-libs so both
services share one implementation (see SPEC D6 note).

Behaviour:
- Accepts http and https (both allowed per user decision).
- Rejects ftp/file/javascript/data and URLs without a hostname.
- Resolves the hostname via ``socket.getaddrinfo`` (off-loop via
  ``asyncio.to_thread``) and rejects private, loopback, link-local,
  multicast, reserved, and IPv6 ULA addresses. A dual-stack host with
  even one private A/AAAA record is rejected — prevents an attacker
  from smuggling an internal IP via the secondary address.
- Rejects a fixed list of docker-internal hostnames regardless of what
  DNS returns — belt-and-braces.
- Returns the canonical URL (fragment + default-port stripped, hostname
  lower-cased, empty path → ``/``) suitable for use as a dedup
  ``source_ref``.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse, urlunparse

from app.services.source_extractors.exceptions import (
    InvalidUrlError,
    SSRFBlockedError,
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Hostnames on our docker-compose network. Even when DNS maps one of
# these to a public IP (shouldn't happen, but defensive), reject it.
# Case-insensitive — we lower-case the hostname before comparing.
_DOCKER_INTERNAL_HOSTNAMES = frozenset(
    {
        "docker-socket-proxy",
        "portal-api",
        "knowledge-ingest",
        "crawl4ai",
        "redis",
        "qdrant",
        "retrieval-api",
        "research-api",
        "connector",
        "scribe",
        "mailer",
        "postgres",
        "gitea",
        "librechat",
        "runtime-api",
        "caddy",
    }
)

_DEFAULT_PORTS = {"http": 80, "https": 443}


async def _resolve_host(host: str, timeout: float = 2.0) -> list[str]:
    """Resolve ``host`` to a list of IP address strings.

    Runs the blocking ``getaddrinfo`` call in a thread so it cannot stall
    the event loop. Returns an empty list on resolution failure rather
    than raising — the caller translates empty/timeout into
    ``SSRFBlockedError``.
    """

    def _sync() -> list[str]:
        infos = socket.getaddrinfo(host, None, socket.AF_UNSPEC)
        # sockaddr[0] is always a str for INET/INET6 families — cast keeps pyright
        # happy (the stub widens to ``str | int`` to cover AF_UNIX).
        return [str(info[4][0]) for info in infos]

    return await asyncio.wait_for(asyncio.to_thread(_sync), timeout=timeout)


def _is_private_ip(raw_ip: str) -> bool:
    """Return True if the IP falls in any disallowed range."""
    try:
        # Strip zone identifier (e.g. "fe80::1%eth0") before parsing.
        cleaned = raw_ip.split("%", 1)[0]
        addr = ipaddress.ip_address(cleaned)
    except ValueError:
        return True  # unparseable → assume unsafe
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def canonicalise_url(url: str) -> str:
    """Return the canonical form of ``url`` for use as a dedup key.

    - Lower-case the hostname (RFC 3986 case-insensitivity).
    - Strip the fragment (client-side navigation only).
    - Strip default ports (80 for http, 443 for https).
    - Preserve the query string (different queries = different pages).
    - An empty path is normalised to "/".
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")

    # Reconstruct netloc without default-port or userinfo.
    netloc = hostname
    if parsed.port is not None and parsed.port != _DEFAULT_PORTS.get(parsed.scheme):
        netloc = f"{hostname}:{parsed.port}"

    path = parsed.path or "/"

    canonical = urlunparse(
        (
            parsed.scheme,
            netloc,
            path,
            "",  # params (legacy RFC 2396)
            parsed.query,
            "",  # fragment
        )
    )
    return canonical


async def validate_url(url: str) -> str:
    """Validate ``url`` and return its canonical form.

    Raises:
        InvalidUrlError: malformed URL, missing scheme, missing hostname,
            or disallowed scheme.
        SSRFBlockedError: hostname is on the docker-internal deny list,
            or any resolved IP falls in a private/loopback/reserved range,
            or DNS resolution failed / timed out / returned no addresses.
    """
    if not isinstance(url, str) or not url.strip():
        raise InvalidUrlError("URL is empty")

    parsed = urlparse(url.strip())
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise InvalidUrlError(f"URL scheme must be http or https, got {parsed.scheme!r}")

    # Normalise hostname: lower-case (RFC 3986) and strip FQDN trailing dot
    # so `http://redis./api` does NOT bypass the docker-internal deny list.
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if not hostname:
        raise InvalidUrlError("URL has no hostname")

    if hostname in _DOCKER_INTERNAL_HOSTNAMES:
        raise SSRFBlockedError("URL targets an internal service")

    try:
        ips = await _resolve_host(hostname)
    except (TimeoutError, OSError) as exc:
        raise SSRFBlockedError(f"DNS resolution failed: {exc}") from exc

    if not ips:
        raise SSRFBlockedError("DNS returned no addresses")

    for ip in ips:
        if _is_private_ip(ip):
            raise SSRFBlockedError("URL resolves to a private or reserved address")

    return canonicalise_url(url)
