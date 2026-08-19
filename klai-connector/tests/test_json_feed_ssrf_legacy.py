"""Legacy-row SSRF contract for the JSON feed adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from klai_image_storage.url_guard import _reset_dns_cache

from app.services.url_guard import (
    SSRF_PERSISTED_JSON_FEED_ERROR,
    PersistedUrlRejectedError,
    validate_json_feed_url_strict,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _reset_dns_cache()


async def test_private_legacy_url_uses_stable_json_feed_error_code() -> None:
    with pytest.raises(PersistedUrlRejectedError) as exc_info:
        await validate_json_feed_url_strict(
            "https://10.0.0.5/feed.json?token=placeholder",
            connector_id="connector-123",
        )

    assert exc_info.value.error_code == SSRF_PERSISTED_JSON_FEED_ERROR
    assert exc_info.value.hostname == "10.0.0.5"
    assert "placeholder" not in str(exc_info.value)


async def test_public_legacy_url_returns_pinned_resolution() -> None:
    with patch(
        "klai_image_storage.url_guard._resolve_blocking",
        return_value=("93.184.216.34",),
    ):
        validated = await validate_json_feed_url_strict(
            "https://data.example.com/feed.json",
            connector_id="connector-123",
        )

    assert validated.hostname == "data.example.com"
    assert validated.preferred_ip == "93.184.216.34"
