"""Tests for the tenant matcher service."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.tenant_matcher import clear_cache, find_tenant


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Clear cache before each test."""
    clear_cache()


@pytest.mark.asyncio
async def test_known_email_returns_user_and_org() -> None:
    """A registered email returns (zitadel_user_id, org_id)."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-123", "zorg-456")

    mock_scalar = AsyncMock(return_value=42)
    mock_session = AsyncMock()
    mock_session.scalar = mock_scalar
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("alice@example.com")

    assert result == ("user-123", 42)
    mock_zitadel.find_user_by_email.assert_awaited_once_with("alice@example.com")


@pytest.mark.asyncio
async def test_unknown_email_returns_none() -> None:
    """An unregistered email returns None."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = None

    with patch("app.services.tenant_matcher.zitadel", mock_zitadel):
        result = await find_tenant("unknown@example.com")

    assert result is None


@pytest.mark.asyncio
async def test_cache_prevents_second_zitadel_call() -> None:
    """Second call for the same email uses cache, not Zitadel."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-123", "zorg-456")

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=42)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result1 = await find_tenant("alice@example.com")
        result2 = await find_tenant("alice@example.com")

    assert result1 == result2 == ("user-123", 42)
    # Zitadel should only be called once -- second call is cached
    mock_zitadel.find_user_by_email.assert_awaited_once()
