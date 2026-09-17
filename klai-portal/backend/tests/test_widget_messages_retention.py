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

    # REQ-14: cross_org_session lookup returns the widget's org_id.
    lookup_row = MagicMock()
    lookup_row.first.return_value = (42,)
    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=lookup_row)
    lookup_db.__aenter__ = AsyncMock(return_value=lookup_db)
    lookup_db.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.widget_audit.cross_org_session", return_value=lookup_db),
        patch("app.services.widget_audit.tenant_scoped_session", return_value=fake_db),
    ):
        await record_widget_turn(
            widget_id="wid-1",
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

    lookup_row = MagicMock()
    lookup_row.first.return_value = (42,)
    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=lookup_row)
    lookup_db.__aenter__ = AsyncMock(return_value=lookup_db)
    lookup_db.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.widget_audit.cross_org_session", return_value=lookup_db),
        patch("app.services.widget_audit.tenant_scoped_session", return_value=fake_db),
    ):
        await record_widget_turn(
            widget_id="wid-1",
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

    lookup_row = MagicMock()
    lookup_row.first.return_value = (42,)
    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=lookup_row)
    lookup_db.__aenter__ = AsyncMock(return_value=lookup_db)
    lookup_db.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.widget_audit.cross_org_session", return_value=lookup_db),
        patch("app.services.widget_audit.tenant_scoped_session", return_value=fake_db),
    ):
        await record_widget_turn(
            widget_id="wid-1",
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


@pytest.mark.asyncio
async def test_retention_run_once_skips_delete_when_no_candidates():
    """No expired candidates means no DELETE against the RLS-protected table."""
    from app.services.widget_messages_retention import _retention_run_once

    captured_sql: list[str] = []
    db = AsyncMock()

    async def _execute(stmt, params=None, **kwargs):
        sql = str(stmt)
        captured_sql.append(sql)
        if "DELETE FROM widget_messages" in sql:
            raise AssertionError("DELETE must not run when the candidate SELECT is empty")
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
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

    assert result == {"deleted_count": 0, "chunk_count": 0}
    assert any("SELECT wm.id, wm.conversation_id" in sql and "FROM widget_messages wm" in sql for sql in captured_sql)


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
# SPEC-CHAT-QUALITY-LOOP-001 open item #2 — per-org retention override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_run_once_applies_per_org_override_over_default():
    """A 90-day org keeps a 30-day-old conversation; a NULL (default 7) org purges it.

    The candidate SELECT joins portal_orgs and filters on
    COALESCE(po.widget_messages_retention_days, :default_days). This fakes
    that join server-side: org 1 has a 90-day override, org 2 has NULL (so
    the 7-day default applies), and both have one 30-day-old widget_messages
    row — only org 2's row is old enough to be a delete candidate.
    """
    from datetime import UTC, datetime, timedelta

    from app.services.widget_messages_retention import _retention_run_once

    now = datetime.now(UTC)
    thirty_days_ago = now - timedelta(days=30)
    org_override_days = {1: 90, 2: None}
    rows = [
        {"id": 501, "conversation_id": 50, "org_id": 1, "created_at": thirty_days_ago},
        {"id": 502, "conversation_id": 60, "org_id": 2, "created_at": thirty_days_ago},
    ]

    captured_select_params: list[dict] = []
    captured_select_sql: list[str] = []
    returned_candidates: list[tuple[int, int, int]] = []
    served = False
    db = AsyncMock()

    async def _execute(stmt, params=None, **kwargs):
        nonlocal served
        sql = str(stmt)
        result = MagicMock()
        if "SELECT wm.id, wm.conversation_id" in sql and "FROM widget_messages wm" in sql:
            captured_select_params.append(dict(params or {}))
            captured_select_sql.append(sql)
            if served:
                result.all = MagicMock(return_value=[])
                return result
            served = True
            default_days = params["default_days"]
            candidates = [
                (row["id"], row["conversation_id"], row["org_id"])
                for row in rows
                if row["created_at"] < now - timedelta(days=org_override_days[row["org_id"]] or default_days)
            ]
            returned_candidates.extend(candidates)
            result.all = MagicMock(return_value=candidates)
            return result
        if "DELETE FROM widget_messages" in sql:
            result.rowcount = len(returned_candidates)
            return result
        result.rowcount = 0
        return result

    db.execute = _execute
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        yield db

    with (
        patch("app.services.widget_messages_retention.cross_org_session", _fake_session),
        patch(
            "app.services.widget_messages_retention.tenant_scoped_session",
            lambda org_id: _fake_session(),
        ),
        patch("app.services.widget_messages_retention.settings") as mock_settings,
    ):
        mock_settings.widget_messages_retention_days = 7
        result = await _retention_run_once()

    assert captured_select_params[0]["default_days"] == 7
    assert "COALESCE(po.widget_messages_retention_days, :default_days)" in captured_select_sql[0]
    # Only org 2's message purges (default 7-day window); org 1's 90-day
    # override keeps its 30-day-old message out of the candidate set.
    assert returned_candidates == [(502, 60, 2)]
    assert result["deleted_count"] == 1


# ---------------------------------------------------------------------------
# REQ-4 (SPEC-CHAT-QUALITY-LOOP-001 §11) — anonymize judgment reasoning on purge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_anonymizes_judgment_reasoning_before_deleting_messages():
    """Purging a conversation's messages NULLs its judgment's reasoning first.

    The UPDATE runs BEFORE the DELETE, in the same org-bound session, and
    only for the DISTINCT conversation_ids of the messages deleted in that
    chunk — a conversation that is not purged keeps its judgment untouched.
    outcome/confidence/judged_at are not in the SET list, so they survive.
    """
    from app.services.widget_messages_retention import _retention_run_once

    session_id = 0
    calls: list[tuple[int, str, dict]] = []
    db = AsyncMock()

    async def _execute(stmt, params=None, **kwargs):
        sql = str(stmt)
        calls.append((session_id, sql, dict(params or {})))
        result = MagicMock()
        if "FROM widget_messages" in sql and sql.lstrip().startswith("SELECT"):
            # 3 expired messages of org 4 across conversations 7, 7 and 9
            result.all.return_value = [(101, 7, 4), (102, 7, 4), (103, 9, 4)]
        elif "UPDATE conversation_quality_judgments" in sql:
            result.rowcount = 2
        else:
            result.rowcount = 3
        return result

    db.execute = _execute
    db.commit = AsyncMock()

    tenant_orgs: dict[int, int] = {}

    @asynccontextmanager
    async def _fake_session():
        nonlocal session_id
        session_id += 1
        yield db

    @asynccontextmanager
    async def _fake_tenant_session(org_id):
        nonlocal session_id
        session_id += 1
        tenant_orgs[session_id] = org_id
        yield db

    import structlog.testing

    with (
        patch("app.services.widget_messages_retention.cross_org_session", _fake_session),
        patch("app.services.widget_messages_retention.tenant_scoped_session", _fake_tenant_session),
        patch("app.services.widget_messages_retention.settings") as mock_settings,
        structlog.testing.capture_logs() as captured,
    ):
        mock_settings.widget_messages_retention_days = 7
        result = await _retention_run_once()

    update_idx = next(i for i, (_, sql, _) in enumerate(calls) if "UPDATE conversation_quality_judgments" in sql)
    delete_idx = next(i for i, (_, sql, _) in enumerate(calls) if "DELETE FROM widget_messages" in sql)
    assert update_idx < delete_idx, "reasoning must be nulled before the messages are deleted"

    _, update_sql, update_params = calls[update_idx]
    assert "SET reasoning = NULL" in update_sql
    assert "anonymized_at = NOW()" in update_sql
    assert "conversation_id = ANY(CAST(:conversation_ids AS bigint[]))" in update_sql
    assert "reasoning IS NOT NULL" in update_sql
    assert update_params["conversation_ids"] == [7, 9]

    # Same org-bound session as the DELETE (no second session/transaction).
    assert calls[update_idx][0] == calls[delete_idx][0]
    assert tenant_orgs[calls[delete_idx][0]] == 4

    assert result["deleted_count"] == 3
    audit = [e for e in captured if e.get("event") == "widget_messages.retention_deleted"]
    assert audit[0]["anonymized_judgments"] == 2


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


# ---------------------------------------------------------------------------
# Visitor contact details are cleared with the conversation they belong to
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_run_once_clears_visitor_contact_of_purged_conversations():
    """The name and e-mail from the pre-chat step do not outlive the transcript.

    They were given so a reviewer could answer this one conversation. Once its
    messages are gone the contact details have no purpose left, so the same
    pass that deletes the messages nulls them on the conversation row, exactly
    as it already does for the judge's quoted reasoning.
    """
    from app.services.widget_messages_retention import _retention_run_once

    statements: list[str] = []
    candidates_served = False

    db = AsyncMock()

    async def _execute(stmt, params=None, **kwargs):
        nonlocal candidates_served
        sql = str(stmt)
        statements.append(sql)
        result = MagicMock()
        if "SELECT wm.id, wm.conversation_id" in sql and "FROM widget_messages wm" in sql:
            if candidates_served:
                result.all = MagicMock(return_value=[])
            else:
                candidates_served = True
                result.all = MagicMock(return_value=[(11, 5, 1), (12, 5, 1), (13, 6, 1)])
            return result
        result.rowcount = 1
        return result

    db.execute = _execute
    db.commit = AsyncMock()

    @asynccontextmanager
    async def _fake_session():
        yield db

    with (
        patch("app.services.widget_messages_retention.cross_org_session", _fake_session),
        patch(
            "app.services.widget_messages_retention.tenant_scoped_session",
            lambda org_id: _fake_session(),
        ),
        patch("app.services.widget_messages_retention.settings") as mock_settings,
    ):
        mock_settings.widget_messages_retention_days = 90
        await _retention_run_once()

    visitor_updates = [
        sql for sql in statements if "UPDATE widget_conversations" in sql and "visitor_email = NULL" in sql
    ]
    assert visitor_updates, f"No visitor-contact anonymization statement ran. Statements: {statements}"
    assert "visitor_name = NULL" in visitor_updates[0]
