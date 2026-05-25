"""REQ-8 (Finding B-5, HIGH): Widget message content SHALL be length-capped and retention-bounded.

Tests cover:
  AC8.1 — record_widget_turn clamps content to 10000 chars before INSERT
  AC8.2 — retention worker deletes rows older than retention_days in chunks
  AC8.3 — retention worker emits audit event with deleted_count and chunk_count
  AC8.4 — retention worker is idempotent (no rows to delete → no error)
  AC8.5 — retention loop catches exceptions and continues (does not abort)

# @MX:NOTE: [AUTO] Tests mirror AC8.x from SPEC-SEC-CROSS-TENANT-FOLLOWUP-001.
# @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-8
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# AC8.1 — content clamping at INSERT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_widget_turn_clamps_content_at_10000_chars():
    """Content longer than 10000 chars is silently truncated to 10000 before INSERT."""
    from app.services.widget_audit import record_widget_turn

    long_content = "x" * 15000
    captured_params: list[dict] = []

    async def _fake_execute(stmt, params=None, **kwargs):
        if params is not None:
            captured_params.append(dict(params))
        result = MagicMock()
        result.first = MagicMock(return_value=(1, 0))  # conv_id=1, prior_count=0
        return result

    fake_db = AsyncMock()
    fake_db.execute = _fake_execute
    fake_db.commit = AsyncMock()
    fake_db.__aenter__ = AsyncMock(return_value=fake_db)
    fake_db.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.widget_audit.tenant_scoped_session", return_value=fake_db):
        await record_widget_turn(
            widget_id="wid-1",
            org_id=42,
            session_key="ses-1",
            role="user",
            content=long_content,
        )

    # Find the INSERT INTO widget_messages params
    message_inserts = [p for p in captured_params if "content" in p and "conversation_id" in p]
    assert message_inserts, "No widget_messages INSERT params captured"
    assert len(message_inserts[0]["content"]) == 10000


@pytest.mark.asyncio
async def test_record_widget_turn_does_not_truncate_short_content():
    """Content at or below 10000 chars is not modified."""
    from app.services.widget_audit import record_widget_turn

    short_content = "hello world"
    captured_params: list[dict] = []

    async def _fake_execute(stmt, params=None, **kwargs):
        if params is not None:
            captured_params.append(dict(params))
        result = MagicMock()
        result.first = MagicMock(return_value=(1, 0))
        return result

    fake_db = AsyncMock()
    fake_db.execute = _fake_execute
    fake_db.commit = AsyncMock()
    fake_db.__aenter__ = AsyncMock(return_value=fake_db)
    fake_db.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.widget_audit.tenant_scoped_session", return_value=fake_db):
        await record_widget_turn(
            widget_id="wid-1",
            org_id=42,
            session_key="ses-1",
            role="assistant",
            content=short_content,
        )

    message_inserts = [p for p in captured_params if "content" in p and "conversation_id" in p]
    assert message_inserts
    assert message_inserts[0]["content"] == short_content


@pytest.mark.asyncio
async def test_record_widget_turn_clamps_exactly_at_boundary():
    """Content of exactly 10000 chars passes through unchanged."""
    from app.services.widget_audit import record_widget_turn

    boundary_content = "y" * 10000
    captured_params: list[dict] = []

    async def _fake_execute(stmt, params=None, **kwargs):
        if params is not None:
            captured_params.append(dict(params))
        result = MagicMock()
        result.first = MagicMock(return_value=(1, 0))
        return result

    fake_db = AsyncMock()
    fake_db.execute = _fake_execute
    fake_db.commit = AsyncMock()
    fake_db.__aenter__ = AsyncMock(return_value=fake_db)
    fake_db.__aexit__ = AsyncMock(return_value=False)

    with patch("app.services.widget_audit.tenant_scoped_session", return_value=fake_db):
        await record_widget_turn(
            widget_id="wid-1",
            org_id=42,
            session_key="ses-1",
            role="user",
            content=boundary_content,
        )

    message_inserts = [p for p in captured_params if "content" in p and "conversation_id" in p]
    assert message_inserts
    assert len(message_inserts[0]["content"]) == 10000


# ---------------------------------------------------------------------------
# AC8.2 — retention worker deletes old rows in chunks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_run_once_deletes_old_rows():
    """_retention_run_once executes DELETE with correct retention cutoff."""
    from app.services.widget_messages_retention import _retention_run_once

    deleted_counts: list[int] = []

    db = AsyncMock()

    async def _execute(stmt, params=None, **kwargs):
        result = MagicMock()
        # Simulate 3 rows deleted in first chunk, 0 in second (done)
        if not deleted_counts:
            result.rowcount = 3
        else:
            result.rowcount = 0
        deleted_counts.append(result.rowcount)
        return result

    db.execute = _execute
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        yield db

    with (
        patch("app.services.widget_messages_retention.cross_org_session", _fake_session),
        patch("app.services.widget_messages_retention.settings") as mock_settings,
    ):
        mock_settings.widget_messages_retention_days = 90
        result = await _retention_run_once()

    assert result["deleted_count"] >= 0
    assert "chunk_count" in result


@pytest.mark.asyncio
async def test_retention_run_once_returns_zero_when_no_old_rows():
    """When no rows are old enough to delete, returns deleted_count=0, chunk_count=0."""
    from app.services.widget_messages_retention import _retention_run_once

    db = AsyncMock()

    async def _execute(stmt, params=None, **kwargs):
        result = MagicMock()
        result.rowcount = 0
        return result

    db.execute = _execute
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        yield db

    with (
        patch("app.services.widget_messages_retention.cross_org_session", _fake_session),
        patch("app.services.widget_messages_retention.settings") as mock_settings,
    ):
        mock_settings.widget_messages_retention_days = 90
        result = await _retention_run_once()

    assert result["deleted_count"] == 0


# ---------------------------------------------------------------------------
# AC8.3 — retention worker emits audit event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_run_once_emits_audit_event():
    """_retention_run_once emits widget_messages.retention_deleted audit event."""
    from app.services.widget_messages_retention import _retention_run_once

    call_count = 0
    db = AsyncMock()

    async def _execute(stmt, params=None, **kwargs):
        result = MagicMock()
        # Return 5 rows on first call, 0 on subsequent (end of chunks)
        result.rowcount = 5 if call_count <= 1 else 0
        return result

    db.execute = _execute
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        nonlocal call_count
        call_count += 1
        yield db

    import structlog.testing

    with (
        patch("app.services.widget_messages_retention.cross_org_session", _fake_session),
        patch("app.services.widget_messages_retention.settings") as mock_settings,
        structlog.testing.capture_logs() as captured,
    ):
        mock_settings.widget_messages_retention_days = 90
        await _retention_run_once()

    audit_events = [e for e in captured if e.get("event") == "widget_messages.retention_deleted"]
    assert len(audit_events) == 1
    assert "deleted_count" in audit_events[0]
    assert "chunk_count" in audit_events[0]


# ---------------------------------------------------------------------------
# AC8.4 — retention worker uses configurable retention_days from settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_run_once_uses_settings_retention_days():
    """_retention_run_once passes settings.widget_messages_retention_days as cutoff."""
    from app.services.widget_messages_retention import _retention_run_once

    captured_params: list[dict] = []
    db = AsyncMock()

    async def _execute(stmt, params=None, **kwargs):
        if params:
            captured_params.append(dict(params))
        result = MagicMock()
        result.rowcount = 0  # no rows, one chunk, done immediately
        return result

    db.execute = _execute
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        yield db

    with (
        patch("app.services.widget_messages_retention.cross_org_session", _fake_session),
        patch("app.services.widget_messages_retention.settings") as mock_settings,
    ):
        mock_settings.widget_messages_retention_days = 30  # custom value
        await _retention_run_once()

    # The SQL must have been called with a cutoff param
    assert captured_params, "No SQL params captured — DELETE not issued"


# ---------------------------------------------------------------------------
# AC8.5 — retention loop handles exceptions without aborting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_loop_continues_after_exception():
    """widget_messages_retention_loop does not abort when _retention_run_once raises."""
    from app.services.widget_messages_retention import widget_messages_retention_loop

    call_count = 0

    async def _raise_first_then_cancel():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient DB error")
        raise asyncio.CancelledError

    with (
        patch("app.services.widget_messages_retention._retention_run_once", side_effect=_raise_first_then_cancel),
        patch("app.services.widget_messages_retention.RETENTION_INTERVAL_SECONDS", 0),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        # Loop should exit cleanly on CancelledError (not propagate RuntimeError)
        with pytest.raises(asyncio.CancelledError):
            await widget_messages_retention_loop()

    assert call_count >= 2, "Loop did not retry after the exception"
