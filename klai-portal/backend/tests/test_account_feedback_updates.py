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
        self.flushes = 0

    async def execute(self, query):
        self.calls += 1
        self.query = query
        self.queries.append(query)
        if self.calls > 1:
            return _Result([])
        return _Result(self.rows)

    async def commit(self):
        self.commits += 1

    async def flush(self):
        self.flushes += 1


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
                message_thread_id=77,
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
    assert item.message_thread_id == 77
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


class _MessageSession:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.commits = 0
        self.flushes = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def execute(self, _query):
        return _Result(self.rows)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1
        for row in self.added:
            if isinstance(row, app_account.PlatformMessageThread) and row.id is None:
                row.id = 987

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_account_feedback_updates_reply_creates_feedback_message_thread(monkeypatch):
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    feedback_session = _Session(
        [
            SimpleNamespace(
                submission_id=123,
                raw_text="De accountpagina laadt niet goed.",
                item_id=456,
                item_title="Accountpagina laadt niet",
            )
        ]
    )
    message_session = _MessageSession([])
    detail = app_account.AccountPlatformMessageThreadDetailOut(
        thread=app_account.AccountPlatformMessageThreadOut(
            id=987,
            subject="Accountpagina laadt niet",
            status="open",
            origin_type="feedback_submission",
            feedback_submission_id=123,
            feedback_item_id=456,
            latest_message_body="Ik heb nog extra context.",
            latest_message_sender_type="user",
            latest_message_at=now,
            last_read_at=None,
            unread=False,
            created_at=now,
        ),
        messages=[],
    )

    async def fake_load_caller_user(*_args, **_kwargs):
        return SimpleNamespace(email="user@example.com", display_name="User")

    async def fake_load_detail(db, **kwargs):
        assert db is message_session
        assert kwargs == {"thread_id": 987, "org_id": 42, "user_id": "user-123"}
        return detail

    monkeypatch.setattr(app_account, "_load_caller_user", fake_load_caller_user)
    monkeypatch.setattr(app_account, "cross_org_session", lambda: message_session)
    monkeypatch.setattr(app_account, "_load_account_message_thread_detail", fake_load_detail)

    result = await app_account.reply_to_feedback_update(
        123,
        app_account.AccountPlatformMessageReplyIn(body="Ik heb nog extra context."),
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=feedback_session,
    )

    thread = next(row for row in message_session.added if isinstance(row, app_account.PlatformMessageThread))
    participant = next(row for row in message_session.added if isinstance(row, app_account.PlatformMessageParticipant))
    message = next(row for row in message_session.added if isinstance(row, app_account.PlatformMessage))
    assert thread.origin_type == "feedback_submission"
    assert thread.feedback_submission_id == 123
    assert thread.feedback_item_id == 456
    assert participant.user_id == "user-123"
    assert message.sender_type == "user"
    assert message.body == "Ik heb nog extra context."
    assert message_session.commits == 1
    assert result.thread.id == 987


@pytest.mark.asyncio
async def test_account_feedback_reply_works_without_linked_item(monkeypatch):
    # Regression for feedback item #18 ("send reply doet niets in Mijn meldingen"):
    # a report with no linked feedback_item must still be repliable (no 404), with
    # the thread subject falling back to the first line of the raw report.
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    feedback_session = _Session(
        [
            SimpleNamespace(
                submission_id=321,
                raw_text="Knop reageert niet.\nTweede regel.",
                item_id=None,
                item_title=None,
            )
        ]
    )
    message_session = _MessageSession([])
    detail = app_account.AccountPlatformMessageThreadDetailOut(
        thread=app_account.AccountPlatformMessageThreadOut(
            id=987,
            subject="Knop reageert niet.",
            status="open",
            origin_type="feedback_submission",
            feedback_submission_id=321,
            feedback_item_id=None,
            latest_message_body="Hier is meer info",
            latest_message_sender_type="user",
            latest_message_at=now,
            last_read_at=None,
            unread=False,
            created_at=now,
        ),
        messages=[],
    )

    async def fake_load_caller_user(*_args, **_kwargs):
        return SimpleNamespace(email="user@example.com", display_name="User")

    async def fake_load_detail(db, **_kwargs):
        assert db is message_session
        return detail

    monkeypatch.setattr(app_account, "_load_caller_user", fake_load_caller_user)
    monkeypatch.setattr(app_account, "cross_org_session", lambda: message_session)
    monkeypatch.setattr(app_account, "_load_account_message_thread_detail", fake_load_detail)

    result = await app_account.reply_to_feedback_update(
        321,
        app_account.AccountPlatformMessageReplyIn(body="Hier is meer info"),
        perms=SimpleNamespace(org_id=42, user_id="user-9"),
        db=feedback_session,
    )

    thread = next(row for row in message_session.added if isinstance(row, app_account.PlatformMessageThread))
    message = next(row for row in message_session.added if isinstance(row, app_account.PlatformMessage))
    assert thread.feedback_submission_id == 321
    assert thread.feedback_item_id is None
    assert thread.subject == "Knop reageert niet."
    assert message.sender_type == "user"
    assert message.body == "Hier is meer info"
    assert message_session.commits == 1
    assert result.thread.id == 987


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


def test_account_platform_message_query_compiles_without_auto_correlation():
    query = app_account._account_thread_select().where(
        app_account.PlatformMessageParticipant.org_id == 42,
        app_account.PlatformMessageParticipant.user_id == "user-123",
    )

    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "platform_message_participants.org_id = 42" in compiled
    assert "SELECT platform_messages.body" in compiled


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


@pytest.mark.asyncio
async def test_account_platform_messages_can_mark_all_unread_threads_read():
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    unread = SimpleNamespace(last_read_at=None)
    already_read = SimpleNamespace(last_read_at=now)
    session = _Session([(unread, now), (already_read, now)])

    result = await app_account.mark_all_platform_messages_read(
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert result.read_count == 1
    assert unread.last_read_at == result.read_at
    assert already_read.last_read_at == now
    assert session.commits == 1


@pytest.mark.asyncio
async def test_account_platform_messages_reply_commits_after_service_helper(monkeypatch):
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    session = _Session([])
    calls = {}

    async def fake_can_access(db, **kwargs):
        assert db is session
        calls["access"] = kwargs
        return True

    async def fake_reply(db, **kwargs):
        assert db is session
        assert session.commits == 0
        calls["reply"] = kwargs
        return SimpleNamespace(
            id=2,
            sender_type="user",
            sender_user_id="user-123",
            body=kwargs["body"],
            created_at=now,
        )

    monkeypatch.setattr(app_account, "user_can_access_thread", fake_can_access)
    monkeypatch.setattr(app_account, "add_platform_message_reply", fake_reply)

    result = await app_account.reply_to_platform_message_thread(
        99,
        app_account.AccountPlatformMessageReplyIn(body="Dank voor je bericht."),
        perms=SimpleNamespace(org_id=42, user_id="user-123"),
        db=session,
    )

    assert calls["access"] == {"thread_id": 99, "org_id": 42, "user_id": "user-123"}
    assert calls["reply"] == {
        "thread_id": 99,
        "org_id": 42,
        "sender_type": "user",
        "sender_user_id": "user-123",
        "body": "Dank voor je bericht.",
    }
    assert result.message.body == "Dank voor je bericht."
    assert session.commits == 1
