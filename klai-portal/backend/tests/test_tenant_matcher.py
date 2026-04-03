"""Tests for the tenant matcher service."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tenant_matcher import SCRIBE_PLANS, clear_cache, find_tenant


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Clear cache before each test."""
    clear_cache()


def _make_org_row(org_id: int = 42, plan: str = "professional") -> SimpleNamespace:
    """Create a fake DB row with id and plan attributes."""
    return SimpleNamespace(id=org_id, plan=plan)


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
async def test_known_email_returns_user_and_org() -> None:
    """A registered email on a scribe plan returns (zitadel_user_id, org_id)."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-123", "zorg-456")

    mock_session = _mock_session_with_org(_make_org_row(42, "professional"))

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

    mock_session = _mock_session_with_org(_make_org_row(42, "complete"))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result1 = await find_tenant("alice@example.com")
        result2 = await find_tenant("alice@example.com")

    assert result1 == result2 == ("user-123", 42)
    # Zitadel should only be called once -- second call is cached
    mock_zitadel.find_user_by_email.assert_awaited_once()


# --- AC-14a: Plan check tests ---


@pytest.mark.asyncio
async def test_plan_professional_allowed() -> None:
    """A user on the 'professional' plan is allowed (AC-14a)."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-1", "zorg-1")

    mock_session = _mock_session_with_org(_make_org_row(10, "professional"))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("pro@example.com")

    assert result == ("user-1", 10)


@pytest.mark.asyncio
async def test_plan_complete_allowed() -> None:
    """A user on the 'complete' plan is allowed (AC-14a)."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-2", "zorg-2")

    mock_session = _mock_session_with_org(_make_org_row(20, "complete"))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("complete@example.com")

    assert result == ("user-2", 20)


@pytest.mark.asyncio
async def test_plan_core_rejected() -> None:
    """A user on the 'core' plan is rejected -- no scribe feature (AC-14a)."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-3", "zorg-3")

    mock_session = _mock_session_with_org(_make_org_row(30, "core"))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("core@example.com")

    assert result is None


@pytest.mark.asyncio
async def test_plan_free_rejected() -> None:
    """A user on the 'free' plan is rejected (AC-14a)."""
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-4", "zorg-4")

    mock_session = _mock_session_with_org(_make_org_row(40, "free"))

    with (
        patch("app.services.tenant_matcher.zitadel", mock_zitadel),
        patch("app.services.tenant_matcher.AsyncSessionLocal", return_value=mock_session),
    ):
        result = await find_tenant("free@example.com")

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


def test_scribe_plans_constant() -> None:
    """SCRIBE_PLANS contains exactly the expected plans."""
    assert SCRIBE_PLANS == {"professional", "complete"}
