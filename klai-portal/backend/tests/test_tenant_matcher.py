"""Tests for the tenant matcher service.

SPEC-PORTAL-PLAN-RENAME-001: scribe gating moved from a plan-bound
allowlist (SCRIBE_PLANS = {"professional", "complete"}) to a per-org
add-on toggle (``portal_orgs.enabled_addons`` contains ``"scribe"``).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tenant_matcher import SCRIBE_ADDON, clear_cache, find_tenant


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Clear cache before each test."""
    clear_cache()


def _make_org_row(org_id: int = 42, enabled_addons: list[str] | None = None) -> SimpleNamespace:
    """Create a fake DB row with id and enabled_addons attributes."""
    return SimpleNamespace(id=org_id, enabled_addons=enabled_addons or [])


def _mock_session_with_org(org_row: SimpleNamespace | None = None) -> AsyncMock:
    """Build a mock async session that returns org_row from execute().one_or_none().

    execute() is async (returns a coroutine), but one_or_none() on the Result is sync.
    """
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = org_row

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


@pytest.mark.asyncio
async def test_known_email_with_scribe_addon_returns_user_and_org() -> None:
    """A registered email whose org has the scribe add-on enabled returns
    (zitadel_user_id, org_id)."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-123", "zorg-456")

    mock_session = _mock_session_with_org(_make_org_row(42, enabled_addons=["scribe"]))

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

    mock_session = _mock_session_with_org(_make_org_row(42, enabled_addons=["scribe"]))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result1 = await find_tenant("alice@example.com")
        result2 = await find_tenant("alice@example.com")

    assert result1 == result2 == ("user-123", 42)
    # Zitadel should only be called once -- second call is cached
    mock_zitadel.find_user_by_email.assert_awaited_once()


# --- SPEC-PORTAL-PLAN-RENAME-001: scribe-as-add-on gating ---


@pytest.mark.asyncio
async def test_org_with_scribe_addon_allowed() -> None:
    """An org with ``scribe`` in enabled_addons is allowed."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-1", "zorg-1")

    mock_session = _mock_session_with_org(_make_org_row(10, enabled_addons=["scribe"]))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("scribe-on@example.com")

    assert result == ("user-1", 10)


@pytest.mark.asyncio
async def test_org_with_scribe_and_other_addons_allowed() -> None:
    """An org with multiple add-ons including scribe is allowed."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-2", "zorg-2")

    mock_session = _mock_session_with_org(_make_org_row(20, enabled_addons=["scribe", "docs"]))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("multi-addon@example.com")

    assert result == ("user-2", 20)


@pytest.mark.asyncio
async def test_org_without_scribe_addon_rejected() -> None:
    """An org whose enabled_addons does not contain ``scribe`` is rejected."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-3", "zorg-3")

    mock_session = _mock_session_with_org(_make_org_row(30, enabled_addons=["docs"]))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("no-scribe@example.com")

    assert result is None


@pytest.mark.asyncio
async def test_org_with_no_addons_rejected() -> None:
    """An org with an empty enabled_addons list is rejected."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-4", "zorg-4")

    mock_session = _mock_session_with_org(_make_org_row(40, enabled_addons=[]))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("no-addons@example.com")

    assert result is None


@pytest.mark.asyncio
async def test_no_portal_org_returns_none() -> None:
    """A Zitadel user with no matching PortalOrg returns None."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-5", "zorg-missing")

    mock_session = _mock_session_with_org(None)

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("orphan@example.com")

    assert result is None


def test_scribe_addon_constant() -> None:
    """The exported SCRIBE_ADDON constant matches the canonical add-on name."""
    assert SCRIBE_ADDON == "scribe"
