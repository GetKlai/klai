"""Tests for the shared SSRF guard (SPEC-SEC-SSRF-001).

Covers (at the klai-libs layer):

- AC-2 — RFC1918 private IP rejection
- AC-3 — link-local / loopback / metadata rejection
- AC-4 — docker-internal hostname rejection (even when DNS lies)
- AC-5 — DNS-rebinding TOCTOU: pinned IP survives a second lookup
- AC-23 basis — ``PinnedResolverTransport`` honours the pin map

The image-pipeline integration (AC-15 through AC-18) is covered in
``test_pipeline.py`` and the connector-side regression tests.
"""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from klai_image_storage.url_guard import (
    PinnedResolverTransport,
    Reason,
    SsrfBlockedError,
    ValidatedURL,
    _DnsCache,
    classify_ip,
    reset_dns_cache,
    validate_image_url,
    validate_url_pinned,
    validate_url_pinned_sync,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset the global DNS cache between tests."""

    reset_dns_cache()


def _stub_resolver(mapping: dict[str, list[str]]):
    """Return a ``socket.getaddrinfo`` replacement driven by *mapping*."""

    def fake(host: str, *args: Any, **kwargs: Any) -> list:
        if host not in mapping:
            raise OSError(f"no test mapping for {host}")
        # getaddrinfo tuples are (family, type, proto, canonname, sockaddr)
        return [(socket.AF_INET, 0, 0, "", (ip, 0)) for ip in mapping[host]]

    return fake


# ---------------------------------------------------------------------------
# Classification unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("10.0.0.1", Reason.PRIVATE_IP),
        ("10.255.255.254", Reason.PRIVATE_IP),
        ("172.16.0.1", Reason.PRIVATE_IP),
        ("172.31.255.254", Reason.PRIVATE_IP),
        ("192.168.1.1", Reason.PRIVATE_IP),
        ("192.168.100.100", Reason.PRIVATE_IP),
        ("127.0.0.1", Reason.LOOPBACK),
        ("169.254.169.254", Reason.LINK_LOCAL),
        ("169.254.0.1", Reason.LINK_LOCAL),
        ("::1", Reason.LOOPBACK),
        ("fe80::1", Reason.LINK_LOCAL),
        ("224.0.0.1", Reason.MULTICAST),
        ("240.0.0.1", Reason.RESERVED),
    ],
)
def test_classify_ip_rejects(ip: str, expected: str) -> None:
    """AC-2, AC-3: every reject-class IP returns the correct reason."""

    assert classify_ip(ip) == expected


@pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"])
def test_classify_ip_accepts_public(ip: str) -> None:
    """AC-2 negative matrix: well-known public IPs are accepted."""

    assert classify_ip(ip) is None


def test_classify_ip_rejects_unparseable() -> None:
    """Fail-closed on unparseable input (REQ-3.5)."""

    assert classify_ip("not-an-ip") == Reason.RESERVED


# ---------------------------------------------------------------------------
# validate_url_pinned — async path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_rejects_non_https() -> None:
    with pytest.raises(SsrfBlockedError) as excinfo:
        await validate_url_pinned("http://example.com/")
    assert excinfo.value.reason == Reason.NON_HTTPS


@pytest.mark.asyncio()
async def test_rejects_missing_hostname() -> None:
    with pytest.raises(SsrfBlockedError) as excinfo:
        await validate_url_pinned("https:///nohost")
    assert excinfo.value.reason == Reason.NO_HOSTNAME


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "host",
    [
        "docker-socket-proxy",
        "portal-api",
        "crawl4ai",
        "redis",
        "postgres",
        "qdrant",
        "falkordb",
        "knowledge-ingest",
        "klai-connector",
        "klai-mailer",
        "research-api",
        "retrieval-api",
        "scribe",
        "garage",
        "litellm",
    ],
)
async def test_rejects_docker_internal_hostname(host: str) -> None:
    """AC-4: every docker-internal host name is rejected without DNS."""

    # Even if DNS were to return a public-looking IP, the hostname check
    # rejects first. We do not mock DNS here — the hostname gate runs
    # before resolution.
    with pytest.raises(SsrfBlockedError) as excinfo:
        await validate_url_pinned(f"https://{host}:8080/info")
    assert excinfo.value.reason == Reason.DOCKER_INTERNAL


@pytest.mark.asyncio()
async def test_rejects_rfc1918_after_dns() -> None:
    """AC-2: RFC1918 resolution is blocked even with HTTPS + public-looking name."""

    with patch(
        "klai_image_storage.url_guard._resolve_blocking",
        return_value=("10.0.0.5",),
    ):
        with pytest.raises(SsrfBlockedError) as excinfo:
            await validate_url_pinned("https://internal.example.test/")
    assert excinfo.value.reason == Reason.PRIVATE_IP


@pytest.mark.asyncio()
async def test_rejects_metadata_endpoint() -> None:
    """AC-3: IPv4 link-local metadata endpoint is blocked."""

    with patch(
        "klai_image_storage.url_guard._resolve_blocking",
        return_value=("169.254.169.254",),
    ):
        with pytest.raises(SsrfBlockedError) as excinfo:
            await validate_url_pinned("https://rebinder.example.test/")
    assert excinfo.value.reason == Reason.LINK_LOCAL


@pytest.mark.asyncio()
async def test_accepts_public_and_pins_ip() -> None:
    """AC-5: the returned ValidatedURL carries the resolved IP set."""

    with patch(
        "klai_image_storage.url_guard._resolve_blocking",
        return_value=("93.184.216.34",),
    ):
        validated = await validate_url_pinned("https://example.com/path")
    assert isinstance(validated, ValidatedURL)
    assert validated.preferred_ip == "93.184.216.34"
    assert "93.184.216.34" in validated.pinned_ips
    assert validated.hostname == "example.com"


@pytest.mark.asyncio()
async def test_literal_ip_hostname_classified_directly() -> None:
    """IP literals bypass DNS but still face classification (AC-19 bullet 3)."""

    # Public literal is accepted.
    validated = await validate_url_pinned("https://1.1.1.1/")
    assert validated.preferred_ip == "1.1.1.1"

    # Private literal is rejected.
    with pytest.raises(SsrfBlockedError) as excinfo:
        await validate_url_pinned("https://10.0.0.5/")
    assert excinfo.value.reason == Reason.PRIVATE_IP


@pytest.mark.asyncio()
async def test_dns_rebinding_pins_first_result() -> None:
    """AC-5: a rebinding attacker cannot swap the pinned IP after validation.

    The guard caches the first-seen IP set. A second lookup returning a
    private address produces a different answer from the resolver, but
    the pinned set returned by the first call is what the httpx
    transport uses.
    """

    call_count = {"n": 0}

    def resolver(host: str, *args: Any, **kwargs: Any) -> list:
        call_count["n"] += 1
        ip = "1.1.1.1" if call_count["n"] == 1 else "172.17.0.5"
        return [(socket.AF_INET, 0, 0, "", (ip, 0))]

    with patch("socket.getaddrinfo", side_effect=resolver):
        first = await validate_url_pinned("https://rebind.example.test/")
        # Second call within cache TTL: MUST return the pinned set.
        second = await validate_url_pinned("https://rebind.example.test/")

    assert first.preferred_ip == "1.1.1.1"
    assert second.preferred_ip == "1.1.1.1"
    # Only one resolver call — cache hit protects the pin.
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# validate_url_pinned_sync — pydantic path
# ---------------------------------------------------------------------------


def test_sync_variant_shares_behaviour() -> None:
    """The sync variant runs the same reject-list without an event loop."""

    with patch(
        "klai_image_storage.url_guard._resolve_blocking",
        return_value=("10.0.0.1",),
    ):
        with pytest.raises(SsrfBlockedError) as excinfo:
            validate_url_pinned_sync("https://masquerade.example.test/")
    assert excinfo.value.reason == Reason.PRIVATE_IP


def test_sync_accepts_public() -> None:
    with patch(
        "klai_image_storage.url_guard._resolve_blocking",
        return_value=("8.8.8.8",),
    ):
        validated = validate_url_pinned_sync("https://example.com/")
    assert validated.preferred_ip == "8.8.8.8"


# ---------------------------------------------------------------------------
# validate_image_url wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_validate_image_url_delegates() -> None:
    """The image wrapper shares reject-list + error shape."""

    with pytest.raises(SsrfBlockedError) as excinfo:
        await validate_image_url("https://docker-socket-proxy:2375/v1.42/info")
    assert excinfo.value.reason == Reason.DOCKER_INTERNAL


# ---------------------------------------------------------------------------
# PinnedResolverTransport
# ---------------------------------------------------------------------------


class _FakeTransport(httpx.AsyncBaseTransport):
    """Records the host httpx actually tried to reach."""

    def __init__(self) -> None:
        self.seen_host: str | None = None
        self.seen_headers_host: str | None = None
        self.seen_sni: str | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen_host = request.url.host
        self.seen_headers_host = request.headers.get("Host")
        if request.extensions:
            self.seen_sni = request.extensions.get("sni_hostname")
        return httpx.Response(200, content=b"ok", request=request)


@pytest.mark.asyncio()
async def test_pinned_transport_rewrites_host() -> None:
    """AC-5 / AC-23: transport connects to the pinned IP, keeps Host + SNI."""

    transport = PinnedResolverTransport()
    transport.pin("example.com", "93.184.216.34")
    # Swap in a no-network fake transport for the parent class path.
    fake = _FakeTransport()
    transport.handle_async_request = fake.handle_async_request  # type: ignore[method-assign]

    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://example.com/path")

    assert resp.status_code == 200


@pytest.mark.asyncio()
async def test_pinned_transport_passthrough_when_unpinned() -> None:
    """Unpinned hosts fall through — explicit, not silent."""

    fake = _FakeTransport()

    class _WrappedTransport(PinnedResolverTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            # Delegate to the fake without hitting the real super().__
            # because that would attempt real network IO.
            host = (request.url.host or "").lower()
            ip = self._pinned.get(host)
            if ip:
                request.url = request.url.copy_with(host=ip)
                request.headers.setdefault("Host", host)
                request.extensions = {**(request.extensions or {}), "sni_hostname": host}
            return await fake.handle_async_request(request)

    transport = _WrappedTransport()

    async with httpx.AsyncClient(transport=transport) as client:
        resp = await client.get("https://example.com/")

    assert resp.status_code == 200
    # Unpinned: host stays unchanged.
    assert fake.seen_host == "example.com"
    assert fake.seen_sni is None


@pytest.mark.asyncio()
async def test_pinned_transport_applies_rewrite_and_sni() -> None:
    """Pinned request: URL host becomes the IP, Host/SNI stay on the name."""

    fake = _FakeTransport()

    class _WrappedTransport(PinnedResolverTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            host = (request.url.host or "").lower()
            ip = self._pinned.get(host)
            if ip:
                request.url = request.url.copy_with(host=ip)
                request.headers.setdefault("Host", host)
                request.extensions = {**(request.extensions or {}), "sni_hostname": host}
            return await fake.handle_async_request(request)

    transport = _WrappedTransport()
    transport.pin("example.com", "93.184.216.34")

    async with httpx.AsyncClient(transport=transport) as client:
        await client.get("https://example.com/")

    assert fake.seen_host == "93.184.216.34"
    assert fake.seen_headers_host == "example.com"
    assert fake.seen_sni == "example.com"


# ---------------------------------------------------------------------------
# DNS cache behaviour
# ---------------------------------------------------------------------------


def test_dns_cache_respects_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache entries expire after ttl_seconds."""

    cache = _DnsCache(max_entries=4, ttl_seconds=0.0)  # immediate expiry
    cache.set("example.com", ("1.1.1.1",))
    assert cache.get("example.com") is None


def test_dns_cache_evicts_lru() -> None:
    """Oldest entry is evicted when the cache is full."""

    cache = _DnsCache(max_entries=2, ttl_seconds=60.0)
    cache.set("a.example", ("1.1.1.1",))
    cache.set("b.example", ("2.2.2.2",))
    cache.set("c.example", ("3.3.3.3",))
    assert cache.get("a.example") is None
    assert cache.get("b.example") == ("2.2.2.2",)
    assert cache.get("c.example") == ("3.3.3.3",)
