"""Specification tests for PortalClient.update_credentials -- SPEC-KB-025.

Covers the token writeback contract used by OAuth adapters when they
refresh an access token.
"""

# ruff: noqa: S106  -- test-only placeholder token strings

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.portal_client import PortalClient


def _make_settings() -> SimpleNamespace:
    return SimpleNamespace(
        portal_api_url="http://portal-api:8100",
        portal_internal_secret="placeholder-internal-value",
    )


def _mock_httpx_client(response: MagicMock) -> MagicMock:
    """Context-manager httpx.AsyncClient substitute."""
    client = MagicMock()
    client.patch = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=client)


# ---------------------------------------------------------------------------
# 1. PATCH payload + URL + auth header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_credentials_sends_patch_with_expected_payload() -> None:
    """update_credentials issues PATCH with access_token and Bearer auth header."""
    settings = _make_settings()
    client = PortalClient(settings)  # type: ignore[arg-type]

    response = MagicMock()
    response.status_code = 204
    response.raise_for_status = MagicMock(return_value=None)

    with patch(
        "app.services.portal_client.httpx.AsyncClient",
        _mock_httpx_client(response),
    ) as mock_cls:
        await client.update_credentials(
            connector_id="conn-uuid-1",
            access_token="placeholder-new-access-value",
            token_expiry="2026-04-16T12:00:00+00:00",
        )

    http_client = mock_cls.return_value
    http_client.patch.assert_awaited_once()
    call = http_client.patch.call_args
    url = call.args[0] if call.args else call.kwargs.get("url")
    assert url == "http://portal-api:8100/internal/connectors/conn-uuid-1/credentials"

    headers = call.kwargs.get("headers", {})
    assert headers.get("Authorization") == "Bearer placeholder-internal-value"

    json_body = call.kwargs.get("json", {})
    assert json_body.get("access_token") == "placeholder-new-access-value"
    assert json_body.get("token_expiry") == "2026-04-16T12:00:00+00:00"


# ---------------------------------------------------------------------------
# 2. Swallows errors (best-effort writeback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_credentials_swallows_errors() -> None:
    """Writeback is best-effort: HTTP errors must not propagate to the caller."""
    settings = _make_settings()
    client = PortalClient(settings)  # type: ignore[arg-type]

    failing_client = MagicMock()
    failing_client.patch = AsyncMock(side_effect=RuntimeError("portal down"))
    failing_client.__aenter__ = AsyncMock(return_value=failing_client)
    failing_client.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "app.services.portal_client.httpx.AsyncClient",
        MagicMock(return_value=failing_client),
    ):
        # Must NOT raise despite the simulated portal outage.
        await client.update_credentials(
            connector_id="conn-uuid-2",
            access_token="placeholder-access-value",
        )


# ---------------------------------------------------------------------------
# 3. Refresh-token rotation (SPEC-KB-MS-DOCS-001 R9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_credentials_includes_refresh_token_when_provided() -> None:
    """When refresh_token kwarg is set, PATCH body includes refresh_token field."""
    settings = _make_settings()
    client = PortalClient(settings)  # type: ignore[arg-type]

    response = MagicMock()
    response.status_code = 204
    response.raise_for_status = MagicMock(return_value=None)

    with patch(
        "app.services.portal_client.httpx.AsyncClient",
        _mock_httpx_client(response),
    ) as mock_cls:
        await client.update_credentials(
            connector_id="conn-uuid-3",
            access_token="placeholder-new-access-value",
            token_expiry="2026-04-23T12:00:00+00:00",
            refresh_token="placeholder-rotated-refresh-value",
        )

    http_client = mock_cls.return_value
    json_body = http_client.patch.call_args.kwargs.get("json", {})
    assert json_body.get("access_token") == "placeholder-new-access-value"
    assert json_body.get("refresh_token") == "placeholder-rotated-refresh-value"
    assert json_body.get("token_expiry") == "2026-04-23T12:00:00+00:00"


@pytest.mark.asyncio
async def test_update_credentials_omits_refresh_token_when_none() -> None:
    """Backward-compat: when refresh_token is None/omitted, PATCH body has no refresh_token key."""
    settings = _make_settings()
    client = PortalClient(settings)  # type: ignore[arg-type]

    response = MagicMock()
    response.status_code = 204
    response.raise_for_status = MagicMock(return_value=None)

    with patch(
        "app.services.portal_client.httpx.AsyncClient",
        _mock_httpx_client(response),
    ) as mock_cls:
        await client.update_credentials(
            connector_id="conn-uuid-4",
            access_token="placeholder-access-value",
            refresh_token=None,
        )

    http_client = mock_cls.return_value
    json_body = http_client.patch.call_args.kwargs.get("json", {})
    assert "refresh_token" not in json_body, "refresh_token must not appear when kwarg is None"
