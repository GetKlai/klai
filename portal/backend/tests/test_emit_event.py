"""Tests for the emit_event fire-and-forget utility."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.events import emit_event


@pytest.mark.asyncio
async def test_emit_event_returns_immediately():
    """emit_event must return synchronously without awaiting the insert."""
    insert_started = asyncio.Event()
    insert_blocker = asyncio.Event()

    async def slow_commit(*args, **kwargs):
        insert_started.set()
        await insert_blocker.wait()

    mock_session = AsyncMock()
    mock_session.commit = slow_commit
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.events.AsyncSessionLocal", return_value=mock_session):
        emit_event("test.event", org_id=1, user_id="user-abc", properties={"key": "value"})
        # Function must have returned already — the insert is background
        assert not insert_started.is_set(), "emit_event blocked on the insert"

    insert_blocker.set()
    await asyncio.sleep(0)  # let background task run


@pytest.mark.asyncio
async def test_emit_event_failure_does_not_raise():
    """A failed insert must be swallowed; the caller must never see an exception."""
    mock_session = AsyncMock()
    mock_session.commit.side_effect = Exception("DB is down")
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.events.AsyncSessionLocal", return_value=mock_session):
        emit_event("test.event", org_id=None, user_id=None)
        # No exception raised — good.

    # Allow the background task to complete (and fail silently)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_emit_event_inserts_correct_data():
    """The inserted ProductEvent must reflect the arguments passed to emit_event."""
    added_events = []

    mock_session = AsyncMock()
    mock_session.add = MagicMock(side_effect=added_events.append)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.events.AsyncSessionLocal", return_value=mock_session):
        emit_event("signup", org_id=42, user_id="zit-123", properties={"plan": "professional"})
        await asyncio.sleep(0.05)

    assert len(added_events) == 1
    event = added_events[0]
    assert event.event_type == "signup"
    assert event.org_id == 42
    assert event.user_id == "zit-123"
    assert event.properties == {"plan": "professional"}
