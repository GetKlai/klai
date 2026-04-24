"""Legacy-row SSRF guard for the Confluence adapter.

SPEC-SEC-SSRF-001 REQ-8.4 / AC-21: a pre-existing ``connector.connectors``
row whose ``config.base_url`` is today SSRF-unsafe (stored before REQ-8
landed in the portal) MUST fail the sync run with the stable error code
``ssrf_blocked_persisted_confluence_base_url`` and MUST NOT construct
an ``atlassian.Confluence`` client.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from klai_image_storage.url_guard import reset_dns_cache

from app.adapters.confluence import ConfluenceAdapter
from app.core.config import Settings
from app.services.url_guard import (
    SSRF_PERSISTED_CONFLUENCE_ERROR,
    PersistedUrlRejected,
    validate_confluence_base_url_strict,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_dns_cache()


class TestConfluenceBaseUrlStrict:
    def test_docker_internal_rejected(self) -> None:
        with pytest.raises(PersistedUrlRejected) as excinfo:
            validate_confluence_base_url_strict(
                "http://portal-api:8010/", connector_id="abc"
            )
        assert excinfo.value.error_code == SSRF_PERSISTED_CONFLUENCE_ERROR

    def test_non_atlassian_host_rejected(self) -> None:
        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=("93.184.216.34",),
        ):
            with pytest.raises(PersistedUrlRejected) as excinfo:
                validate_confluence_base_url_strict(
                    "https://attacker.example.com/wiki", connector_id="abc"
                )
        assert excinfo.value.error_code == SSRF_PERSISTED_CONFLUENCE_ERROR

    def test_ip_literal_private_rejected(self) -> None:
        with pytest.raises(PersistedUrlRejected):
            validate_confluence_base_url_strict(
                "https://10.0.0.5/wiki", connector_id="abc"
            )

    def test_atlassian_tenant_passes(self) -> None:
        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=("104.192.136.1",),
        ):
            validate_confluence_base_url_strict(
                "https://klai.atlassian.net", connector_id="abc"
            )


class TestAdapterExtractConfig:
    """``_extract_config`` is the load-time hook; it MUST fail before
    any ``atlassian.Confluence(...)`` client is instantiated."""

    def _connector(self, base_url: str) -> SimpleNamespace:
        return SimpleNamespace(
            id="abc-123",
            config={
                "base_url": base_url,
                "email": "x@y.com",
                "api_token": "t",
            },
        )

    def test_legacy_internal_base_url_blocks_client(self) -> None:
        connector = self._connector("http://confluence-internal:8090/")

        with patch("app.adapters.confluence.Confluence") as sdk:
            with pytest.raises(PersistedUrlRejected) as excinfo:
                ConfluenceAdapter._extract_config(connector)
        # CRITICAL: the SDK client MUST NOT have been constructed.
        assert sdk.call_count == 0
        assert excinfo.value.error_code == SSRF_PERSISTED_CONFLUENCE_ERROR

    def test_valid_tenant_passes_and_returns_config(self) -> None:
        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=("104.192.136.1",),
        ):
            connector = self._connector("https://klai.atlassian.net")
            cfg = ConfluenceAdapter._extract_config(connector)
        assert cfg["base_url"] == "https://klai.atlassian.net"
        assert cfg["email"] == "x@y.com"
