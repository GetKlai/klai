"""SPEC-SEC-HYGIENE-001 REQ-27 / AC-27: tenant_matcher cache TTL must be
short enough that a plan downgrade reflects within a minute.

Pre-fix CACHE_TTL was 5 minutes, which meant a tenant downgrading from
`professional` to `free` could still send invite-bot meeting traffic for
up to 5 minutes after the downgrade landed (the cache held the old
plan-eligible result). Business-logic hygiene fix: shrink the TTL to 60
seconds (Option A from the SPEC — preferred for simplicity over an
explicit invalidate_cache hook on the plan-change path).

Tests:
- The CACHE_TTL constant equals 60 seconds (REQ-27.1 Option A choice).
- Behavioural: an expired cache entry is re-fetched, so a plan
  downgrade is reflected on the second call (REQ-27.3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import tenant_matcher
from app.services.tenant_matcher import CACHE_TTL, clear_cache, find_tenant


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_cache()


def test_cache_ttl_is_sixty_seconds() -> None:
    """REQ-27.1 (Option A): the cache TTL is reduced from 5 minutes to 60 seconds."""
    assert CACHE_TTL == timedelta(seconds=60), (
        "SPEC-SEC-HYGIENE-001 REQ-27.1 Option A requires CACHE_TTL == 60s; "
        f"current value is {CACHE_TTL!r}. The 5-minute window let a downgraded "
        "tenant continue to receive scribe traffic — see SPEC for the rationale."
    )


def _mock_session_with_org(plan: str) -> AsyncMock:
    org_row = SimpleNamespace(id=42, plan=plan)
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = org_row
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


@pytest.mark.asyncio
async def test_expired_cache_re_fetches_after_downgrade() -> None:
    """REQ-27.3: after the TTL elapses, the next find_tenant call re-fetches
    Zitadel + plan, so a downgrade from professional → free is reflected.

    Test technique: instead of moving the wall clock, mutate the cache
    expiry to a past timestamp between the two calls. This exercises the
    same `if now < expires:` branch that real time elapsing would exercise.
    """
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-1", "zorg-1")

    # First call: tenant on professional plan → cached
    with (
        patch.object(tenant_matcher, "zitadel", mock_zitadel),
        patch.object(
            tenant_matcher,
            "AsyncSessionLocal",
            return_value=_mock_session_with_org("professional"),
        ),
    ):
        result1 = await find_tenant("alice@example.com")
    assert result1 == ("user-1", 42)
    mock_zitadel.find_user_by_email.assert_awaited_once()

    # Force the cached entry to be expired (simulate >60s elapsed).
    expired_when = datetime.now(UTC) - timedelta(seconds=1)
    tenant_matcher._cache["alice@example.com"] = (result1, expired_when)

    # Second call: same email, but plan has been downgraded to free.
    # Cache expired → re-fetch → plan check fails → returns None.
    with (
        patch.object(tenant_matcher, "zitadel", mock_zitadel),
        patch.object(
            tenant_matcher,
            "AsyncSessionLocal",
            return_value=_mock_session_with_org("free"),
        ),
    ):
        result2 = await find_tenant("alice@example.com")
    assert result2 is None, (
        "Cache expired before the second call; a downgraded plan must make find_tenant return None on the next request."
    )
    # Zitadel was called twice — once for the populated entry, once after expiry.
    assert mock_zitadel.find_user_by_email.await_count == 2
