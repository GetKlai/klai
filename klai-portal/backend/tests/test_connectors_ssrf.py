"""SSRF validator tests for portal connector config schemas.

SPEC-SEC-SSRF-001 — AC-7 / AC-8 (web_crawler), AC-19 / AC-20
(confluence). Tests exercise the pydantic ``model_validator`` and
the shared helper ``_validate_connector_config`` to confirm that:

- base_url / canary_url pointing at RFC1918, docker-internal, or
  non-HTTPS hosts is rejected with a pydantic-style ValueError
  (surfaces as HTTP 422 in the router).
- Confluence base_url outside ``*.atlassian.net`` /
  ``*.atlassian.com`` is rejected with a domain-allowlist error.
- IP literals (``https://10.0.0.5/wiki``) still get SSRF-classified
  even when the domain-allowlist branch is skipped — no bypass via
  IP literal (AC-19 bullet 3).
- Legitimate tenants on ``*.atlassian.net`` still validate OK
  (positive path; no overblock).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from klai_image_storage.url_guard import _reset_dns_cache
from pydantic import ValidationError

from app.api.connectors import (
    ConfluenceConfig,
    WebcrawlerConfig,
    _validate_connector_config,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _reset_dns_cache()


def _patched_resolve(ip: str):
    """Patch klai_image_storage's blocking resolver to return *ip*."""

    return patch(
        "klai_image_storage.url_guard._resolve_blocking",
        return_value=(ip,),
    )


# ---------------------------------------------------------------------------
# WebcrawlerConfig — AC-7 (create), AC-8 (update)
# ---------------------------------------------------------------------------


class TestWebcrawlerConfigSsrf:
    def test_docker_internal_base_url_rejected(self) -> None:
        """AC-7 first bullet: docker-socket-proxy as base_url → 422."""

        with pytest.raises(ValidationError) as excinfo:
            WebcrawlerConfig(base_url="https://docker-socket-proxy:2375/")
        assert "base_url" in str(excinfo.value).lower()

    def test_rfc1918_base_url_rejected(self) -> None:
        """AC-7 second bullet: http://10.0.0.5/ → 422 on private_ip class."""

        with pytest.raises(ValidationError) as excinfo:
            WebcrawlerConfig(base_url="https://10.0.0.5/")
        assert "base_url" in str(excinfo.value).lower()

    def test_canary_url_rejected(self) -> None:
        """AC-7 third bullet: canary_url on internal host → 422 naming canary_url."""

        with _patched_resolve("93.184.216.34"):
            with pytest.raises(ValidationError) as excinfo:
                WebcrawlerConfig(
                    base_url="https://example.com/",
                    canary_url="https://portal-api:8010/secret",
                )
        assert "canary_url" in str(excinfo.value).lower()

    def test_non_https_rejected(self) -> None:
        """HTTP base_url is rejected by the scheme check."""

        with pytest.raises(ValidationError):
            WebcrawlerConfig(base_url="http://example.com/")

    def test_valid_public_url_accepted(self) -> None:
        """Positive path: a genuinely public URL validates."""

        with _patched_resolve("93.184.216.34"):
            cfg = WebcrawlerConfig(base_url="https://example.com/")
        assert cfg.base_url == "https://example.com/"


# ---------------------------------------------------------------------------
# ConfluenceConfig — AC-19 (create), AC-20 (update)
# ---------------------------------------------------------------------------


class TestConfluenceConfigSsrf:
    def test_non_atlassian_domain_rejected(self) -> None:
        """AC-19 second bullet: https://attacker.example.com/wiki → 422."""

        with _patched_resolve("93.184.216.34"):
            with pytest.raises(ValidationError) as excinfo:
                ConfluenceConfig(
                    base_url="https://attacker.example.com/wiki/",
                    email="x@y.com",
                    api_token="t",
                )
        msg = str(excinfo.value).lower()
        assert "atlassian" in msg or "allow" in msg

    def test_docker_internal_rejected(self) -> None:
        """AC-19 first bullet: docker-socket-proxy → 422."""

        with pytest.raises(ValidationError):
            ConfluenceConfig(
                base_url="http://docker-socket-proxy:2375/",
                email="x@y.com",
                api_token="t",
            )

    def test_rfc1918_literal_rejected(self) -> None:
        """AC-19 third bullet: IP literal 10.0.0.5 → 422 even though
        domain-allowlist step is structurally skipped."""

        with pytest.raises(ValidationError):
            ConfluenceConfig(
                base_url="https://10.0.0.5/wiki",
                email="x@y.com",
                api_token="t",
            )

    def test_valid_atlassian_tenant_accepted(self) -> None:
        """AC-19 fourth bullet: legitimate klai-tenant.atlassian.net works."""

        with _patched_resolve("104.192.136.1"):
            cfg = ConfluenceConfig(
                base_url="https://klai-tenant.atlassian.net",
                email="admin@klai.test",
                api_token="ATLASSIAN_TOKEN",
            )
        assert cfg.base_url.endswith(".atlassian.net")


# ---------------------------------------------------------------------------
# _validate_connector_config dispatcher — integration with route handler
# ---------------------------------------------------------------------------


class TestValidateConnectorConfig:
    def test_web_crawler_dispatch_invokes_validator(self) -> None:
        """_validate_connector_config raises HTTPException 422 on SSRF reject."""

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            _validate_connector_config(
                "web_crawler",
                {"base_url": "https://portal-api:8010/"},
            )
        assert excinfo.value.status_code == 422

    def test_confluence_dispatch_invokes_validator(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            _validate_connector_config(
                "confluence",
                {
                    "base_url": "http://evil.example.com/",
                    "email": "x@y.com",
                    "api_token": "t",
                },
            )
        assert excinfo.value.status_code == 422

    def test_unknown_connector_type_passes_through(self) -> None:
        """github / notion / etc. have no SSRF config surface today.

        Pass through unchanged — any future SSRF-relevant connector
        adds itself to ``_CONFIG_SCHEMA`` to opt in.
        """

        out = _validate_connector_config("github", {"repo": "klai/foo"})
        assert out == {"repo": "klai/foo"}
