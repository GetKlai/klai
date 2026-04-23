"""Specification tests for OAuthAdapterBase — SPEC-KB-025 + SPEC-KB-MS-DOCS-001 R9.

Covers the refresh-token cache + writeback contract, with special attention to
providers that rotate refresh tokens on each refresh (Microsoft Graph).
"""

# ruff: noqa: S106  -- test-only placeholder token strings

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.adapters.oauth_base import OAuthAdapterBase


class _FakeOAuthAdapter(OAuthAdapterBase):
    """Test subclass that lets us script the refresh response."""

    def __init__(self, settings: Any, portal_client: Any, refresh_payload: dict[str, Any]) -> None:
        super().__init__(settings=settings, portal_client=portal_client)
        self._refresh_payload = refresh_payload
        self.refresh_call_count = 0

    async def _refresh_oauth_token(
        self, connector: Any, refresh_token: str,
    ) -> dict[str, Any]:
        self.refresh_call_count += 1
        return self._refresh_payload


def _make_connector(connector_id: str, refresh_token: str) -> Any:
    """Minimal connector stub with id + config dict."""
    return SimpleNamespace(
        id=connector_id,
        config={"refresh_token": refresh_token},
    )


@pytest.fixture
def portal_client() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def settings() -> SimpleNamespace:
    return SimpleNamespace()


@pytest.mark.asyncio
async def test_ensure_token_refreshes_and_caches(portal_client: AsyncMock, settings: SimpleNamespace) -> None:
    """First ensure_token call triggers refresh; second uses cached value."""
    adapter = _FakeOAuthAdapter(
        settings=settings,
        portal_client=portal_client,
        refresh_payload={"access_token": "placeholder-access-v1", "expires_in": 3600},
    )
    connector = _make_connector("conn-1", "placeholder-refresh-original")

    token1 = await adapter.ensure_token(connector)
    token2 = await adapter.ensure_token(connector)

    assert token1 == "placeholder-access-v1"
    assert token2 == "placeholder-access-v1"
    assert adapter.refresh_call_count == 1


@pytest.mark.asyncio
async def test_ensure_token_writeback_access_token_only_when_no_rotation(
    portal_client: AsyncMock, settings: SimpleNamespace,
) -> None:
    """Refresh response without new refresh_token → writeback with refresh_token=None."""
    adapter = _FakeOAuthAdapter(
        settings=settings,
        portal_client=portal_client,
        refresh_payload={"access_token": "placeholder-access-v1", "expires_in": 3600},
    )
    connector = _make_connector("conn-2", "placeholder-refresh-original")

    await adapter.ensure_token(connector)

    portal_client.update_credentials.assert_awaited_once()
    kwargs = portal_client.update_credentials.await_args.kwargs
    assert kwargs["connector_id"] == "conn-2"
    assert kwargs["access_token"] == "placeholder-access-v1"
    assert kwargs.get("refresh_token") is None


@pytest.mark.asyncio
async def test_ensure_token_missing_refresh_token_raises(
    portal_client: AsyncMock, settings: SimpleNamespace,
) -> None:
    """No refresh_token in config → ValueError on first refresh attempt."""
    adapter = _FakeOAuthAdapter(
        settings=settings,
        portal_client=portal_client,
        refresh_payload={"access_token": "placeholder-access-v1", "expires_in": 3600},
    )
    connector = SimpleNamespace(id="conn-3", config={})

    with pytest.raises(ValueError, match="missing refresh_token"):
        await adapter.ensure_token(connector)


@pytest.mark.asyncio
async def test_ensure_token_writes_back_rotated_refresh_token(
    portal_client: AsyncMock, settings: SimpleNamespace,
) -> None:
    """Refresh response with new refresh_token → writeback includes refresh_token kwarg."""
    adapter = _FakeOAuthAdapter(
        settings=settings,
        portal_client=portal_client,
        refresh_payload={
            "access_token": "placeholder-access-v1",
            "expires_in": 3600,
            "refresh_token": "placeholder-refresh-rotated",
        },
    )
    connector = _make_connector("conn-4", "placeholder-refresh-original")

    await adapter.ensure_token(connector)

    portal_client.update_credentials.assert_awaited_once()
    kwargs = portal_client.update_credentials.await_args.kwargs
    assert kwargs["access_token"] == "placeholder-access-v1"
    assert kwargs["refresh_token"] == "placeholder-refresh-rotated"


@pytest.mark.asyncio
async def test_ensure_token_mutates_connector_config_on_rotation(
    portal_client: AsyncMock, settings: SimpleNamespace,
) -> None:
    """After rotation, connector.config reflects the new refresh_token."""
    adapter = _FakeOAuthAdapter(
        settings=settings,
        portal_client=portal_client,
        refresh_payload={
            "access_token": "placeholder-access-v1",
            "expires_in": 3600,
            "refresh_token": "placeholder-refresh-rotated",
        },
    )
    connector = _make_connector("conn-5", "placeholder-refresh-original")

    await adapter.ensure_token(connector)

    assert connector.config["refresh_token"] == "placeholder-refresh-rotated"


@pytest.mark.asyncio
async def test_ensure_token_no_writeback_when_refresh_token_unchanged(
    portal_client: AsyncMock, settings: SimpleNamespace,
) -> None:
    """If provider echoes the same refresh_token, treat as no-rotation (no RT writeback)."""
    adapter = _FakeOAuthAdapter(
        settings=settings,
        portal_client=portal_client,
        refresh_payload={
            "access_token": "placeholder-access-v1",
            "expires_in": 3600,
            "refresh_token": "placeholder-refresh-original",
        },
    )
    connector = _make_connector("conn-6", "placeholder-refresh-original")

    await adapter.ensure_token(connector)

    kwargs = portal_client.update_credentials.await_args.kwargs
    assert kwargs.get("refresh_token") is None
