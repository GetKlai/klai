"""Tests for SSRF protection in ``knowledge_ingest.utils.url_validator``.

Post SPEC-SEC-SSRF-001 the module is a thin wrapper over
``klai_image_storage.url_guard``; these tests exercise the wrapper
path (historical ``validate_url``/``is_private_ip``/``validate_url_scheme``
symbols) and the new ``validate_url_pinned`` contract.

Covered acceptance criteria:
- AC-2: RFC1918 matrix (10.x, 172.16-31.x, 192.168.x)
- AC-3: link-local / loopback / metadata
- AC-4: docker-internal hostnames
- AC-5: DNS-rebinding TOCTOU (pinned IP wins)
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any
from unittest.mock import patch

import pytest
from klai_image_storage.url_guard import _reset_dns_cache

from knowledge_ingest.utils.url_validator import (
    is_private_ip,
    validate_url,
    validate_url_pinned,
    validate_url_scheme,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset the shared DNS cache between tests."""

    _reset_dns_cache()


def _getaddrinfo(ips: list[str]) -> list[Any]:
    """Return a fake ``socket.getaddrinfo`` result for *ips*."""

    return [(socket.AF_INET, 0, 0, "", (ip, 0)) for ip in ips]


class TestValidateUrlScheme:
    def test_http_raises(self) -> None:
        with pytest.raises(ValueError, match="Only HTTPS"):
            validate_url_scheme("http://example.com")

    def test_ftp_raises(self) -> None:
        with pytest.raises(ValueError, match="Only HTTPS"):
            validate_url_scheme("ftp://example.com/file")

    def test_https_passes(self) -> None:
        validate_url_scheme("https://example.com")

    def test_empty_scheme_raises(self) -> None:
        with pytest.raises(ValueError):
            validate_url_scheme("://example.com")


class TestIsPrivateIp:
    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.1.1",
            "192.168.100.100",
            "127.0.0.1",
            "169.254.169.254",
            "::1",
            "fe80::1",
            "not-an-ip",
        ],
    )
    def test_rejected(self, ip: str) -> None:
        assert is_private_ip(ip) is True

    @pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "93.184.216.34"])
    def test_accepted(self, ip: str) -> None:
        assert is_private_ip(ip) is False


class TestValidateUrl:
    """Exercises the backwards-compatible ``validate_url`` wrapper."""

    def test_https_public_ip_passes(self) -> None:
        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=("93.184.216.34",),
        ):
            result = asyncio.run(validate_url("https://example.com"))
        assert result == "https://example.com"

    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "172.31.255.254",
            "192.168.1.1",
            "192.168.100.100",
        ],
    )
    def test_rfc1918_matrix_rejected(self, ip: str) -> None:
        """AC-2: every RFC1918 representative is blocked."""

        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=(ip,),
        ):
            with pytest.raises(ValueError, match="forbidden"):
                asyncio.run(validate_url("https://internal.example.test/"))

    def test_loopback_rejected(self) -> None:
        """AC-3: loopback is blocked."""

        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=("127.0.0.1",),
        ):
            with pytest.raises(ValueError, match="forbidden"):
                asyncio.run(validate_url("https://localhost.example.test/"))

    def test_link_local_metadata_rejected(self) -> None:
        """AC-3: AWS / GCP metadata IP is blocked."""

        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=("169.254.169.254",),
        ):
            with pytest.raises(ValueError, match="forbidden"):
                asyncio.run(validate_url("https://metadata.example.test/"))

    def test_ipv6_loopback_rejected(self) -> None:
        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=("::1",),
        ):
            with pytest.raises(ValueError, match="forbidden"):
                asyncio.run(validate_url("https://ipv6-loop.example.test/"))

    def test_dns_failure_rejected(self) -> None:
        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            side_effect=OSError("nxdomain"),
        ):
            with pytest.raises(ValueError, match="DNS resolution failed"):
                asyncio.run(validate_url("https://nxdomain.invalid"))

    def test_no_hostname_rejected(self) -> None:
        with pytest.raises(ValueError, match="no hostname"):
            asyncio.run(validate_url("https://"))

    def test_http_rejected_before_dns(self) -> None:
        with pytest.raises(ValueError, match="Only HTTPS"):
            asyncio.run(validate_url("http://example.com"))


class TestDockerInternalHostnames:
    """AC-4: docker-internal hostnames are rejected without DNS lookup."""

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
    def test_container_name_rejected(self, host: str) -> None:
        with pytest.raises(ValueError):
            asyncio.run(validate_url(f"https://{host}:8080/info"))


class TestDnsRebinding:
    """AC-5: a second DNS lookup cannot swap the pinned IP."""

    def test_pinned_ip_survives_rebind(self) -> None:
        call_count = {"n": 0}

        def resolver(host: str, *args: Any, **kwargs: Any) -> list[Any]:
            call_count["n"] += 1
            ip = "1.1.1.1" if call_count["n"] == 1 else "172.17.0.5"
            return [(socket.AF_INET, 0, 0, "", (ip, 0))]

        async def run() -> tuple[str, str]:
            with patch("socket.getaddrinfo", side_effect=resolver):
                first = await validate_url_pinned("https://rebind.example.test/")
                second = await validate_url_pinned("https://rebind.example.test/")
            return first.preferred_ip, second.preferred_ip

        first_ip, second_ip = asyncio.run(run())
        assert first_ip == "1.1.1.1"
        # Cache hit on the second call — pin holds.
        assert second_ip == "1.1.1.1"
        assert call_count["n"] == 1
