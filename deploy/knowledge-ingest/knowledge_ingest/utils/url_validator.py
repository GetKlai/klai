"""
URL validation utilities for SSRF protection.

Validates URL scheme (HTTPS only) and resolves DNS to block private/reserved IPs.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlparse


def validate_url_scheme(url: str) -> None:
    """Raise ValueError if URL is not HTTPS."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only HTTPS URLs are allowed. Got: {parsed.scheme!r}")


def is_private_ip(ip: str) -> bool:
    """Return True if the IP is private/reserved."""
    try:
        addr = ipaddress.ip_address(ip)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        )
    except ValueError:
        return True  # treat unparseable as unsafe


async def validate_url(url: str, dns_timeout: float = 2.0) -> str:
    """Validate URL scheme and resolve DNS to check for private IPs.

    Raises ValueError with a safe error message if validation fails.
    Returns the (unchanged) URL on success.

    dns_timeout: seconds to wait for DNS resolution before raising ValueError.
    """
    validate_url_scheme(url)

    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL has no hostname")

    def _resolve() -> list:
        return socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)

    try:
        # Run blocking DNS resolution in a thread pool to avoid stalling the event loop
        infos = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, _resolve),
            timeout=dns_timeout,
        )
        ips = {info[4][0] for info in infos}
    except TimeoutError:
        raise ValueError("DNS resolution timed out")
    except OSError as exc:
        raise ValueError(f"DNS resolution failed: {exc}") from exc

    for ip in ips:
        if is_private_ip(ip):
            raise ValueError("URL resolves to a private or reserved IP address")

    return url
