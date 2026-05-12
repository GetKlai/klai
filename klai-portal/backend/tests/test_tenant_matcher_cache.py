"""SPEC-SEC-HYGIENE-001 REQ-27 / AC-27: tenant_matcher cache TTL must be
short enough that an add-on toggle reflects within a minute.

Pre-fix CACHE_TTL was 5 minutes, which meant a tenant disabling the scribe
add-on could still send invite-bot meeting traffic for up to 5 minutes
after the toggle landed (the cache held the old eligible result).
Business-logic hygiene fix: shrink the TTL to 60 seconds (Option A from
the SPEC — preferred for simplicity over an explicit invalidate_cache
hook on the add-on toggle path).

SPEC-PORTAL-PLAN-RENAME-001 update: scribe gating moved from a plan-bound
SCRIBE_PLANS allowlist to a per-org ``enabled_addons`` list. The cache
test now exercises an add-on REMOVAL across the TTL boundary; same
semantic concern, different signal.

Tests:
- The CACHE_TTL constant equals 60 seconds (REQ-27.1 Option A choice).
- Behavioural: an expired cache entry is re-fetched, so an add-on
  toggle is reflected on the next call (REQ-27.3).
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


def _mock_session_with_org(enabled_addons: list[str]) -> AsyncMock:
    org_row = SimpleNamespace(id=42, enabled_addons=enabled_addons)
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = org_row
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


@pytest.mark.asyncio
async def test_expired_cache_re_fetches_after_addon_disabled() -> None:
    """REQ-27.3: after the TTL elapses, the next find_tenant call re-fetches
    Zitadel + add-on state, so disabling the scribe add-on is reflected.

    Test technique: instead of moving the wall clock, mutate the cache
    expiry to a past timestamp between the two calls. This exercises the
    same `if now < expires:` branch that real time elapsing would exercise.
    """
    mock_zitadel = AsyncMock()
    mock_zitadel.find_user_by_email.return_value = ("user-1", "zorg-1")

    # First call: tenant has scribe add-on enabled → cached.
    with (
        patch.object(tenant_matcher, "zitadel", mock_zitadel),
        patch.object(
            tenant_matcher,
            "AsyncSessionLocal",
            return_value=_mock_session_with_org(["scribe"]),
        ),
    ):
        result1 = await find_tenant("alice@example.com")
    assert result1 == ("user-1", 42)
    mock_zitadel.find_user_by_email.assert_awaited_once()

    # Force the cached entry to be expired (simulate >60s elapsed).
    expired_when = datetime.now(UTC) - timedelta(seconds=1)
    tenant_matcher._cache["alice@example.com"] = (result1, expired_when)

    # Second call: same email, but the scribe add-on has been disabled.
    # Cache expired → re-fetch → add-on check fails → returns None.
    with (
        patch.object(tenant_matcher, "zitadel", mock_zitadel),
        patch.object(
            tenant_matcher,
            "AsyncSessionLocal",
            return_value=_mock_session_with_org([]),
        ),
    ):
        result2 = await find_tenant("alice@example.com")
    assert result2 is None, (
        "Cache expired before the second call; disabling the scribe add-on "
        "must make find_tenant return None on the next request."
    )
    # Zitadel was called twice — once for the populated entry, once after expiry.
    assert mock_zitadel.find_user_by_email.await_count == 2
