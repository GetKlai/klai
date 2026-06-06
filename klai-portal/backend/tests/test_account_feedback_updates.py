from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api import app_account


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.query = None
        self.queries = []
        self.commits = 0

    async def execute(self, query):
        self.calls += 1
        self.query = query
        self.queries.append(query)
        if self.calls > 1:
            return _Result([])
        return _Result(self.rows)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_account_feedback_updates_returns_current_user_feedback_reports():
    now = datetime(2026, 5, 28, 10, 0, tzinfo=UTC)
    later = datetime(2026, 5, 28, 11, 0, tzinfo=UTC)
    session = _Session(
        [
            SimpleNamespace(
                submission_id=123,
                source="assistant_problem",
                raw_text="De accountpagina laadt niet goed.",
                submission_status="open",
                created_at=now,
                updated_at=now,
                page_url="https://acme.getklai.com/app/account",
                route_id="/app/account",
                item_id=456,
                item_kind="bug",
                item_title="Accountpagina laadt niet",
                item_summary="Gebruikers zien een fout op de accountpagina.",
                item_status="open",
                item_updated_at=later,
            )
        ]
    )

    result = await app_account.get_feedback_updates(
        limit=500,
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert result.unread_count == 0
    assert len(result.items) == 1
    item = result.items[0]
    assert item.submission_id == 123
    assert item.item_id == 456
    assert item.item_kind == "bug"
    assert item.item_status == "open"
    assert item.latest_update_at == later

    compiled = str(session.queries[0].compile(compile_kwargs={"literal_binds": True}))
    assert "feedback_submissions.org_id = 42" in compiled
    assert "feedback_submissions.user_id = 'user-123'" in compiled
    assert "feedback_submissions.source IN ('assistant_problem', 'assistant_feedback')" in compiled
    assert "LIMIT 100" in compiled


@pytest.mark.asyncio
async def test_account_feedback_updates_can_mark_all_unread_notifications_read():
    notification = SimpleNamespace(read_at=None)
    session = _Session([notification])

    result = await app_account.mark_all_feedback_updates_read(
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert result.read_count == 1
    assert notification.read_at == result.read_at
    assert session.commits == 1


@pytest.mark.asyncio
async def test_account_platform_messages_returns_only_current_user_threads():
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    session = _Session(
        [
            SimpleNamespace(
                id=99,
                subject="Vraag over je feedback",
                status="open",
                origin_type="direct",
                feedback_submission_id=None,
                feedback_item_id=None,
                latest_message_body="Kun je meer context geven?",
                latest_message_sender_type="platform_admin",
                latest_message_at=now,
                created_at=now,
                last_read_at=None,
                latest_admin_at=now,
            )
        ]
    )

    result = await app_account.get_platform_messages(
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert result.unread_count == 1
    assert result.items[0].id == 99
    assert result.items[0].unread is True

    compiled = str(session.queries[0].compile(compile_kwargs={"literal_binds": True}))
    assert "platform_message_participants.org_id = 42" in compiled
    assert "platform_message_participants.user_id = 'user-123'" in compiled


@pytest.mark.asyncio
async def test_account_platform_messages_returns_empty_when_user_has_no_threads():
    session = _Session([])

    result = await app_account.get_platform_messages(
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert result.unread_count == 0
    assert result.items == []


@pytest.mark.asyncio
async def test_account_platform_messages_can_mark_thread_read():
    participant = SimpleNamespace(last_read_at=None)
    session = _Session([participant])

    result = await app_account.mark_platform_message_read(
        99,
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert result.thread_id == 99
    assert participant.last_read_at == result.read_at
    assert session.commits == 1
