"""Tests for the SSRF guard used by URL + YouTube source extractors.

SPEC-KB-SOURCES-001 D6 + R2.1 + R5.3 — block any URL that resolves to
rfc1918, link-local, loopback, IPv6 loopback/link-local/ULA, or known
docker-internal hostnames BEFORE any outbound fetch.
"""

from __future__ import annotations

import socket
from collections.abc import Iterable

import pytest

from app.services.source_extractors._url_validator import (
    canonicalise_url,
    validate_url,
)
from app.services.source_extractors.exceptions import (
    InvalidUrlError,
    SSRFBlockedError,
)


def _fake_resolver(ips: Iterable[str]) -> object:
    """Return a coroutine factory that resolves any host to the given IPs.

    `getaddrinfo` returns tuples of (family, type, proto, canonname, sockaddr).
    We only care about sockaddr[0] (the address string).
    """
    resolved = list(ips)

    async def _resolve(_host: str, _timeout: float = 2.0) -> list[str]:
        return resolved

    return _resolve


class TestSchemeValidation:
    async def test_rejects_missing_scheme(self) -> None:
        with pytest.raises(InvalidUrlError):
            await validate_url("example.com/page")

    async def test_rejects_ftp_scheme(self) -> None:
        with pytest.raises(InvalidUrlError):
            await validate_url("ftp://example.com/file")

    async def test_rejects_file_scheme(self) -> None:
        with pytest.raises(InvalidUrlError):
            await validate_url("file:///etc/passwd")

    async def test_rejects_javascript_scheme(self) -> None:
        with pytest.raises(InvalidUrlError):
            await validate_url("javascript:alert(1)")

    async def test_rejects_url_without_hostname(self) -> None:
        with pytest.raises(InvalidUrlError):
            await validate_url("https:///path")

    async def test_accepts_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Per user decision: both http and https are accepted."""
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["93.184.216.34"]),
        )
        result = await validate_url("http://example.com/page")
        assert result.startswith("http://example.com")

    async def test_accepts_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["93.184.216.34"]),
        )
        result = await validate_url("https://example.com/page")
        assert result.startswith("https://example.com")


class TestIPv4BlockedRanges:
    async def test_rejects_loopback_127_0_0_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["127.0.0.1"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://localhost/")

    async def test_rejects_loopback_127_anything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["127.5.9.2"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://tricky.example.com/")

    async def test_rejects_rfc1918_10_net(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["10.0.5.100"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://internal.example.com/")

    async def test_rejects_rfc1918_172_16(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["172.20.5.10"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://internal.example.com/")

    async def test_accepts_172_15_which_is_public(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 172.16.0.0/12 is private — 172.15.x is outside that, and 172.32.x too.
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["172.15.1.1"]),
        )
        # Should not raise — 172.15.x is public IP space.
        await validate_url("http://public.example.com/")

    async def test_rejects_rfc1918_192_168(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["192.168.1.50"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://router.local/")

    async def test_rejects_link_local_169_254(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # EC2/GCP metadata: 169.254.169.254 — classic SSRF target.
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["169.254.169.254"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://metadata.google.internal/")

    async def test_rejects_literal_ip_input(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An IP literal bypasses DNS but still goes through the validator."""
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["127.0.0.1"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://127.0.0.1/")


class TestIPv6BlockedRanges:
    async def test_rejects_ipv6_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["::1"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://localhost/")

    async def test_rejects_ipv6_link_local_fe80(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["fe80::1"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://host.example.com/")

    async def test_rejects_ipv6_ula_fc00(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["fc00::1"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://host.example.com/")

    async def test_rejects_ipv6_fd00(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # fd00::/8 is in the ULA fc00::/7 supernet.
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["fd12::abcd"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://host.example.com/")

    async def test_accepts_public_ipv6(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["2606:4700:4700::1111"]),  # 1.1.1.1 over IPv6
        )
        result = await validate_url("http://one.one.one.one/")
        assert result.startswith("http://")


class TestDockerInternalHostnames:
    @pytest.mark.parametrize(
        "hostname",
        [
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
        ],
    )
    async def test_rejects_docker_internal_hostname(
        self, hostname: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even if DNS resolved to a public IP, the hostname itself is blocked.
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["93.184.216.34"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url(f"http://{hostname}/api")

    async def test_rejects_docker_internal_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["93.184.216.34"]),
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://KNOWLEDGE-INGEST/api")


class TestDualStackResolution:
    async def test_rejects_if_any_resolved_ip_is_private(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dual-stack: if the host has one public and one private IP, reject."""
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver(["93.184.216.34", "10.0.0.5"]),  # mix public + private
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://sneaky.example.com/")


class TestResolutionFailure:
    async def test_rejects_on_dns_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _raise(_host: str, _timeout: float = 2.0) -> list[str]:
            raise OSError("Name or service not known")

        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _raise,
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://nonexistent.invalid/")

    async def test_rejects_on_dns_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _raise(_host: str, _timeout: float = 2.0) -> list[str]:
            raise TimeoutError("timed out")

        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _raise,
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://slow.example.com/")

    async def test_rejects_on_empty_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors._url_validator._resolve_host",
            _fake_resolver([]),  # no IPs returned
        )
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://empty.example.com/")


class TestCanonicaliseUrl:
    def test_strips_fragment(self) -> None:
        assert canonicalise_url("https://example.com/page#section") == "https://example.com/page"

    def test_strips_default_http_port(self) -> None:
        assert canonicalise_url("http://example.com:80/page") == "http://example.com/page"

    def test_strips_default_https_port(self) -> None:
        assert canonicalise_url("https://example.com:443/page") == "https://example.com/page"

    def test_preserves_non_default_port(self) -> None:
        assert canonicalise_url("https://example.com:8443/page") == "https://example.com:8443/page"

    def test_preserves_query_string(self) -> None:
        # Different queries on the same path are different pages (pagination).
        assert (
            canonicalise_url("https://example.com/archive?page=2")
            == "https://example.com/archive?page=2"
        )

    def test_lowercases_hostname(self) -> None:
        # Hostnames are case-insensitive per RFC 3986.
        assert canonicalise_url("https://EXAMPLE.com/PAGE") == "https://example.com/PAGE"

    def test_empty_path_becomes_slash(self) -> None:
        assert canonicalise_url("https://example.com") == "https://example.com/"


class TestIntegrationWithSocketResolution:
    """Sanity check that the real _resolve_host works for public hosts.

    This does NOT go out to the network — pytest plugin could block it. We
    rely on socket.getaddrinfo being capable of resolving 'localhost' without
    network access. This test will be skipped if that fails.
    """

    async def test_real_resolver_rejects_localhost(self) -> None:
        try:
            socket.getaddrinfo("localhost", None)
        except OSError:
            pytest.skip("localhost not resolvable in this environment")
        with pytest.raises(SSRFBlockedError):
            await validate_url("http://localhost/")
