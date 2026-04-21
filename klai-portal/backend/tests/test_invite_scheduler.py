"""Tests for the invite scheduler service."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ical_parser import ParsedInvite
from app.services.invite_scheduler import _scheduled, cancel_invite, schedule_invite


def _make_invite(
    uid: str = "test-uid@example.com",
    dtstart: datetime | None = None,
    is_cancellation: bool = False,
) -> ParsedInvite:
    """Helper to create a ParsedInvite for testing."""
    if dtstart is None:
        dtstart = datetime.now(UTC) + timedelta(hours=1)
    return ParsedInvite(
        uid=uid,
        organizer_email="organizer@example.com",
        meeting_url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
        native_meeting_id="abc-defg-hij",
        dtstart=dtstart,
        summary="Test Meeting",
        is_cancellation=is_cancellation,
    )


@pytest.fixture(autouse=True)
def _clean_scheduled() -> None:
    """Clear scheduled tasks before each test."""
    for task in _scheduled.values():
        task.cancel()
    _scheduled.clear()


@pytest.mark.asyncio
async def test_past_meeting_skipped() -> None:
    """A meeting with DTSTART in the past is skipped."""
    invite = _make_invite(dtstart=datetime.now(UTC) - timedelta(hours=1))

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.invite_scheduler.cross_org_session", return_value=mock_session):
        await schedule_invite(invite, "user-1", None)

    assert invite.uid not in _scheduled


@pytest.mark.asyncio
async def test_future_meeting_scheduled() -> None:
    """A future meeting creates a scheduled task."""
    invite = _make_invite(dtstart=datetime.now(UTC) + timedelta(hours=2))

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=None)  # no existing meeting
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.invite_scheduler.cross_org_session", return_value=mock_session):
        await schedule_invite(invite, "user-1", 42)

    assert invite.uid in _scheduled
    task = _scheduled[invite.uid]
    assert not task.done()
    # Clean up
    task.cancel()


@pytest.mark.asyncio
async def test_cancellation_cancels_task() -> None:
    """A cancellation invite cancels the scheduled task."""
    uid = "cancel-test@example.com"

    # Pre-schedule a future meeting
    future_invite = _make_invite(uid=uid, dtstart=datetime.now(UTC) + timedelta(hours=2))

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(return_value=None)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.invite_scheduler.cross_org_session", return_value=mock_session):
        await schedule_invite(future_invite, "user-1", None)
        assert uid in _scheduled

        # Now cancel it
        await cancel_invite(uid)

    assert uid not in _scheduled


@pytest.mark.asyncio
async def test_duplicate_invite_skipped() -> None:
    """A duplicate invite (same ical_uid in DB) is skipped."""
    invite = _make_invite(uid="dup@example.com", dtstart=datetime.now(UTC) + timedelta(hours=2))

    mock_session = AsyncMock()
    # simulate existing meeting found in DB
    mock_session.scalar = AsyncMock(return_value="some-uuid")
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.invite_scheduler.cross_org_session", return_value=mock_session):
        await schedule_invite(invite, "user-1", None)

    assert invite.uid not in _scheduled


# --- AC-14b: Rate limit tests ---


@pytest.mark.asyncio
async def test_rate_limit_exceeded_blocks_invite() -> None:
    """An invite is blocked when the user has exceeded the daily rate limit (AC-14b)."""
    invite = _make_invite(uid="rate@example.com", dtstart=datetime.now(UTC) + timedelta(hours=2))

    call_count = 0

    async def _scalar_side_effect(*_args: object, **_kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None  # no duplicate ical_uid
        # _check_rate_limit query returns count >= limit
        return 10  # at limit (default is 10)

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(side_effect=_scalar_side_effect)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.invite_scheduler.cross_org_session", return_value=mock_session):
        await schedule_invite(invite, "user-1", None)

    assert invite.uid not in _scheduled


@pytest.mark.asyncio
async def test_rate_limit_not_exceeded_allows_invite() -> None:
    """An invite is allowed when the user is below the daily rate limit (AC-14b)."""
    invite = _make_invite(uid="ok@example.com", dtstart=datetime.now(UTC) + timedelta(hours=2))

    call_count = 0

    async def _scalar_side_effect(*_args: object, **_kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None  # no duplicate ical_uid
        # _check_rate_limit query returns count below limit
        return 5  # below default limit of 10

    mock_session = AsyncMock()
    mock_session.scalar = AsyncMock(side_effect=_scalar_side_effect)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.invite_scheduler.cross_org_session", return_value=mock_session):
        await schedule_invite(invite, "user-1", 42)

    assert invite.uid in _scheduled
    _scheduled[invite.uid].cancel()
