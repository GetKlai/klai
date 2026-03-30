"""Tests for SSRF protection in url_validator.py (TASK-008)."""
import asyncio
import socket
from unittest.mock import patch

import pytest

from knowledge_ingest.utils.url_validator import is_private_ip, validate_url, validate_url_scheme


class TestValidateUrlScheme:
    def test_http_raises(self):
        with pytest.raises(ValueError, match="Only HTTPS"):
            validate_url_scheme("http://example.com")

    def test_ftp_raises(self):
        with pytest.raises(ValueError, match="Only HTTPS"):
            validate_url_scheme("ftp://example.com/file")

    def test_https_passes(self):
        validate_url_scheme("https://example.com")

    def test_empty_scheme_raises(self):
        with pytest.raises(ValueError):
            validate_url_scheme("://example.com")


class TestIsPrivateIp:
    def test_private_10(self):
        assert is_private_ip("10.0.0.1") is True

    def test_private_172(self):
        assert is_private_ip("172.16.0.1") is True

    def test_private_192(self):
        assert is_private_ip("192.168.1.1") is True

    def test_loopback(self):
        assert is_private_ip("127.0.0.1") is True

    def test_link_local(self):
        assert is_private_ip("169.254.169.254") is True

    def test_ipv6_loopback(self):
        assert is_private_ip("::1") is True

    def test_public_ip(self):
        assert is_private_ip("8.8.8.8") is False

    def test_unparseable(self):
        assert is_private_ip("not-an-ip") is True


def _mock_getaddrinfo(ips):
    """Return a fake socket.getaddrinfo result for the given IPs."""
    return [(socket.AF_INET, 0, 0, "", (ip, 0)) for ip in ips]


class TestValidateUrl:
    def test_https_public_ip_passes(self):
        with patch(
            "knowledge_ingest.utils.url_validator.socket.getaddrinfo",
            return_value=_mock_getaddrinfo(["93.184.216.34"]),
        ):
            result = asyncio.run(validate_url("https://example.com"))
            assert result == "https://example.com"

    def test_resolves_to_private_10_raises(self):
        with patch(
            "knowledge_ingest.utils.url_validator.socket.getaddrinfo",
            return_value=_mock_getaddrinfo(["10.0.0.1"]),
        ):
            with pytest.raises(ValueError, match="private or reserved"):
                asyncio.run(validate_url("https://internal.example.com"))

    def test_resolves_to_loopback_raises(self):
        with patch(
            "knowledge_ingest.utils.url_validator.socket.getaddrinfo",
            return_value=_mock_getaddrinfo(["127.0.0.1"]),
        ):
            with pytest.raises(ValueError, match="private or reserved"):
                asyncio.run(validate_url("https://localhost.example.com"))

    def test_resolves_to_metadata_ip_raises(self):
        with patch(
            "knowledge_ingest.utils.url_validator.socket.getaddrinfo",
            return_value=_mock_getaddrinfo(["169.254.169.254"]),
        ):
            with pytest.raises(ValueError, match="private or reserved"):
                asyncio.run(validate_url("https://metadata.example.com"))

    def test_resolves_to_ipv6_loopback_raises(self):
        with patch(
            "knowledge_ingest.utils.url_validator.socket.getaddrinfo",
            return_value=[(socket.AF_INET6, 0, 0, "", ("::1", 0, 0, 0))],
        ):
            with pytest.raises(ValueError, match="private or reserved"):
                asyncio.run(validate_url("https://ipv6-loopback.example.com"))

    def test_dns_failure_raises(self):
        with patch(
            "knowledge_ingest.utils.url_validator.socket.getaddrinfo",
            side_effect=OSError("DNS failed"),
        ):
            with pytest.raises(ValueError, match="DNS resolution failed"):
                asyncio.run(validate_url("https://nonexistent.invalid"))

    def test_no_hostname_raises(self):
        with pytest.raises(ValueError, match="no hostname"):
            asyncio.run(validate_url("https://"))

    def test_http_rejected_before_dns(self):
        with pytest.raises(ValueError, match="Only HTTPS"):
            asyncio.run(validate_url("http://example.com"))

    def test_dns_timeout_raises(self):
        import time

        def slow_getaddrinfo(*args, **kwargs):
            time.sleep(0.2)
            return _mock_getaddrinfo(["8.8.8.8"])

        with patch(
            "knowledge_ingest.utils.url_validator.socket.getaddrinfo",
            side_effect=slow_getaddrinfo,
        ):
            with pytest.raises(ValueError, match="timed out"):
                asyncio.run(validate_url("https://slow.example.com", dns_timeout=0.1))
