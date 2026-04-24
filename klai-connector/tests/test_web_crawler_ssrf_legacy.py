"""Legacy-row SSRF guard for the web_crawler delegation path.

SPEC-SEC-SSRF-001 REQ-2.4 / AC-9: a pre-existing ``connector.connectors``
row whose ``config.base_url`` or ``config.canary_url`` is today
SSRF-unsafe (stored before the portal ``WebcrawlerConfig`` validator
landed in Fase 5) MUST fail the sync run with the stable error code
``ssrf_blocked_persisted_url`` and MUST NOT delegate to
knowledge-ingest's ``/ingest/v1/crawl/sync`` endpoint.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from klai_image_storage.url_guard import _reset_dns_cache

from app.services.url_guard import (
    SSRF_PERSISTED_ERROR,
    PersistedUrlRejectedError,
    validate_web_crawler_config_strict,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _reset_dns_cache()


class TestValidateWebCrawlerConfigStrict:
    def test_docker_internal_base_url_rejected(self) -> None:
        with pytest.raises(PersistedUrlRejectedError) as excinfo:
            validate_web_crawler_config_strict(
                {"base_url": "https://portal-api:8010/"},
                connector_id="abc",
            )
        assert excinfo.value.error_code == SSRF_PERSISTED_ERROR
        assert "base_url" in str(excinfo.value)

    def test_canary_url_rejected_even_if_base_url_is_fine(self) -> None:
        # base_url resolves to public, canary_url is docker-internal.
        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=("93.184.216.34",),
        ), pytest.raises(PersistedUrlRejectedError) as excinfo:
            validate_web_crawler_config_strict(
                {
                    "base_url": "https://example.com/",
                    "canary_url": "https://redis:6379/",
                },
                connector_id="abc",
            )
        assert "canary_url" in str(excinfo.value)

    def test_rfc1918_literal_rejected(self) -> None:
        with pytest.raises(PersistedUrlRejectedError):
            validate_web_crawler_config_strict(
                {"base_url": "https://10.0.0.5/"},
                connector_id="abc",
            )

    def test_valid_config_passes(self) -> None:
        """Positive path: public base_url + public canary_url validates."""

        with patch(
            "klai_image_storage.url_guard._resolve_blocking",
            return_value=("93.184.216.34",),
        ):
            validate_web_crawler_config_strict(
                {
                    "base_url": "https://docs.example.com/",
                    "canary_url": "https://docs.example.com/health",
                },
                connector_id="abc",
            )

    def test_missing_base_url_passes_through(self) -> None:
        """The guard only validates URLs it sees — missing fields are
        the portal's job (required-field check)."""

        validate_web_crawler_config_strict({}, connector_id="abc")

    def test_non_string_base_url_rejected(self) -> None:
        """Defence in depth: legacy rows with bogus types still fail."""

        with pytest.raises(PersistedUrlRejectedError) as excinfo:
            validate_web_crawler_config_strict(
                {"base_url": 12345},  # type: ignore[dict-item]
                connector_id="abc",
            )
        assert excinfo.value.error_code == SSRF_PERSISTED_ERROR
