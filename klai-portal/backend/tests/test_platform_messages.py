from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.admin import platform_messages
from app.core import database as db_module
from app.platform_messaging import service as messaging_service


class _Session:
    closed = False

    def __init__(self):
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.closed = True
        return None

    async def commit(self):
        self.commits += 1
        return None


def _detail(thread_id=99):
    now = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    return platform_messages.PlatformMessageThreadDetailOut(
        thread=platform_messages.PlatformMessageThreadOut(
            id=thread_id,
            org_id=42,
            org_name="Acme",
            org_slug="acme",
            subject="Vraag over je feedback",
            status="open",
            origin_type="direct",
            feedback_submission_id=None,
            feedback_item_id=None,
            recipient_count=1,
            latest_message_body="Kun je meer context geven?",
            latest_message_sender_type="platform_admin",
            latest_message_at=now,
            latest_user_message_at=None,
            latest_admin_message_at=now,
            unread_for_admin=False,
            created_by="staff",
            created_at=now,
        ),
        recipients=[
            platform_messages.PlatformMessageRecipientOut(
                user_id="user-123",
                email="user@example.com",
                display_name="User",
                last_read_at=None,
            )
        ],
        messages=[
            platform_messages.PlatformMessageOut(
                id=1,
                sender_type="platform_admin",
                sender_user_id="staff",
                body="Kun je meer context geven?",
                created_at=now,
            )
        ],
    )


@pytest.mark.asyncio
async def test_platform_message_thread_create_uses_platform_admin_context(monkeypatch):
    session = _Session()
    calls = {}

    async def fake_audit(*_args, **_kwargs):
        calls["audit"] = True

    async def fake_create(db, **kwargs):
        calls["db"] = db
        calls["kwargs"] = kwargs
        return SimpleNamespace(id=99)

    async def fake_detail(db, thread_id):
        assert db is session
        assert thread_id == 99
        assert session.commits == 0
        return _detail(thread_id)

    monkeypatch.setattr(platform_messages, "_audit", fake_audit)
    monkeypatch.setattr(platform_messages, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform_messages, "create_platform_message_thread", fake_create)
    monkeypatch.setattr(platform_messages, "_load_thread_detail", fake_detail)

    result = await platform_messages.platform_message_thread_create(
        platform_messages.PlatformMessageThreadCreateIn(
            org_id=42,
            user_ids=["user-123"],
            subject="Vraag over je feedback",
            body="Kun je meer context geven?",
        ),
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert calls["audit"] is True
    assert calls["db"] is session
    assert calls["kwargs"]["org_id"] == 42
    assert calls["kwargs"]["user_ids"] == ["user-123"]
    assert calls["kwargs"]["created_by"] == "staff"
    assert result.thread.id == 99
    assert session.commits == 1
    assert session.closed is True


def test_platform_message_thread_search_query_compiles_with_recipient_join():
    query = (
        platform_messages._thread_select()
        .outerjoin(
            platform_messages.PlatformMessageParticipant,
            platform_messages.PlatformMessageParticipant.thread_id == platform_messages.PlatformMessageThread.id,
        )
        .where(platform_messages.PlatformMessageParticipant.recipient_display_name.ilike("%jelle%"))
    )

    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "platform_message_participants" in compiled
    assert "SELECT count(platform_message_participants.user_id)" in compiled


def test_platform_message_thread_detail_query_compiles_without_auto_correlation():
    query = platform_messages._thread_select().where(
        platform_messages.PlatformMessageThread.id == 99,
    )

    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "platform_message_threads.id = 99" in compiled
    assert "SELECT platform_messages.body" in compiled
    assert "platform_messages.sender_type = 'user'" in compiled
    assert "platform_messages.sender_type IN ('platform_admin', 'system')" in compiled


def test_platform_message_thread_out_marks_user_replies_unread_for_admin():
    user_reply_at = datetime(2026, 6, 6, 11, 0, tzinfo=UTC)
    admin_reply_at = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)

    result = platform_messages._thread_out(
        SimpleNamespace(
            id=99,
            org_id=42,
            org_name="Acme",
            org_slug="acme",
            subject="Vraag over je feedback",
            status="open",
            origin_type="direct",
            feedback_submission_id=None,
            feedback_item_id=None,
            recipient_count=1,
            latest_message_body="Reactie",
            latest_message_sender_type="user",
            latest_message_at=user_reply_at,
            latest_user_message_at=user_reply_at,
            latest_admin_message_at=admin_reply_at,
            created_by="staff",
            created_at=admin_reply_at,
        )
    )

    assert result.unread_for_admin is True


def test_platform_message_thread_out_clears_unread_after_admin_reply():
    user_reply_at = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    admin_reply_at = datetime(2026, 6, 6, 11, 0, tzinfo=UTC)

    result = platform_messages._thread_out(
        SimpleNamespace(
            id=99,
            org_id=42,
            org_name="Acme",
            org_slug="acme",
            subject="Vraag over je feedback",
            status="open",
            origin_type="direct",
            feedback_submission_id=None,
            feedback_item_id=None,
            recipient_count=1,
            latest_message_body="Dank",
            latest_message_sender_type="platform_admin",
            latest_message_at=admin_reply_at,
            latest_user_message_at=user_reply_at,
            latest_admin_message_at=admin_reply_at,
            created_by="staff",
            created_at=user_reply_at,
        )
    )

    assert result.unread_for_admin is False


def test_platform_message_thread_out_clears_unread_after_admin_opens_thread():
    # Admin opened (read) the thread after the user's last message, without
    # replying. The unread indicator must clear on read, not only on reply.
    user_reply_at = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    admin_read_at = datetime(2026, 6, 6, 10, 30, tzinfo=UTC)

    result = platform_messages._thread_out(
        SimpleNamespace(
            id=99,
            org_id=42,
            org_name="Acme",
            org_slug="acme",
            subject="Vraag over je feedback",
            status="open",
            origin_type="direct",
            feedback_submission_id=None,
            feedback_item_id=None,
            recipient_count=1,
            latest_message_body="Reactie",
            latest_message_sender_type="user",
            latest_message_at=user_reply_at,
            latest_user_message_at=user_reply_at,
            latest_admin_message_at=None,
            admin_read_at=admin_read_at,
            created_by="staff",
            created_at=user_reply_at,
        )
    )

    assert result.unread_for_admin is False


def test_platform_message_thread_out_unread_when_user_replies_after_admin_read():
    # A new user message after the admin's last read re-marks the thread unread.
    admin_read_at = datetime(2026, 6, 6, 10, 0, tzinfo=UTC)
    user_reply_at = datetime(2026, 6, 6, 11, 0, tzinfo=UTC)

    result = platform_messages._thread_out(
        SimpleNamespace(
            id=99,
            org_id=42,
            org_name="Acme",
            org_slug="acme",
            subject="Vraag over je feedback",
            status="open",
            origin_type="direct",
            feedback_submission_id=None,
            feedback_item_id=None,
            recipient_count=1,
            latest_message_body="Nog een vraag",
            latest_message_sender_type="user",
            latest_message_at=user_reply_at,
            latest_user_message_at=user_reply_at,
            latest_admin_message_at=None,
            admin_read_at=admin_read_at,
            created_by="staff",
            created_at=admin_read_at,
        )
    )

    assert result.unread_for_admin is True


@pytest.mark.asyncio
async def test_platform_message_thread_reply_uses_thread_org(monkeypatch):
    session = _Session()
    calls = {}

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_get_thread(db, thread_id):
        assert db is session
        assert thread_id == 99
        return SimpleNamespace(id=99, org_id=42)

    async def fake_reply(db, **kwargs):
        assert session.commits == 0
        calls["kwargs"] = kwargs
        return SimpleNamespace(id=2)

    async def fake_detail(db, thread_id):
        assert session.commits == 0
        return _detail(thread_id)

    monkeypatch.setattr(platform_messages, "_audit", fake_audit)
    monkeypatch.setattr(platform_messages, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform_messages, "get_platform_message_thread", fake_get_thread)
    monkeypatch.setattr(platform_messages, "add_platform_message_reply", fake_reply)
    monkeypatch.setattr(platform_messages, "_load_thread_detail", fake_detail)

    result = await platform_messages.platform_message_thread_reply(
        99,
        platform_messages.PlatformMessageReplyIn(body="Dank voor je reactie."),
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert calls["kwargs"] == {
        "thread_id": 99,
        "org_id": 42,
        "sender_type": "platform_admin",
        "sender_user_id": "staff",
        "body": "Dank voor je reactie.",
    }
    assert result.thread.id == 99
    assert session.commits == 1


@pytest.mark.asyncio
async def test_platform_message_thread_status_loads_detail_before_commit(monkeypatch):
    session = _Session()
    calls = {}

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_get_thread(db, thread_id):
        assert db is session
        assert thread_id == 99
        thread = SimpleNamespace(id=99, status="open")
        calls["thread"] = thread
        return thread

    async def fake_detail(db, thread_id):
        assert db is session
        assert thread_id == 99
        assert calls["thread"].status == "closed"
        assert session.commits == 0
        return _detail(thread_id)

    monkeypatch.setattr(platform_messages, "_audit", fake_audit)
    monkeypatch.setattr(platform_messages, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform_messages, "get_platform_message_thread", fake_get_thread)
    monkeypatch.setattr(platform_messages, "_load_thread_detail", fake_detail)

    result = await platform_messages.platform_message_thread_status(
        99,
        platform_messages.PlatformMessageStatusIn(status="closed"),
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result.thread.id == 99
    assert session.commits == 1


class _ServiceResult:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _ServiceSession:
    def __init__(self, row):
        self.row = row
        self.added = []
        self.flushes = 0
        self.commits = 0

    async def execute(self, _query):
        return _ServiceResult(self.row)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_add_platform_message_reply_flushes_without_committing():
    thread = SimpleNamespace(id=99, org_id=42, status="closed", last_message_at=None)
    session = _ServiceSession(thread)

    message = await messaging_service.add_platform_message_reply(
        session,
        thread_id=99,
        org_id=42,
        sender_type="platform_admin",
        sender_user_id="staff",
        body="Dank voor je reactie.",
    )

    assert message in session.added
    assert thread.status == "open"
    assert thread.last_message_at == message.created_at
    assert session.flushes == 1
    assert session.commits == 0


@pytest.mark.asyncio
async def test_mark_platform_message_thread_read_flushes_without_committing():
    participant = SimpleNamespace(last_read_at=None)
    session = _ServiceSession(participant)

    read_at = await messaging_service.mark_platform_message_thread_read(
        session,
        thread_id=99,
        org_id=42,
        user_id="user-123",
    )

    assert participant.last_read_at == read_at
    assert session.flushes == 1
    assert session.commits == 0


def test_platform_message_post_deploy_grants_portal_api():
    sql = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "post_deploy_m1n2o3p4q5r6_platform_message_threads_rls.sql"
    ).read_text()

    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON platform_message_threads TO portal_api" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON platform_message_participants TO portal_api" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON platform_messages TO portal_api" in sql
    assert "GRANT USAGE, SELECT ON SEQUENCE platform_message_threads_id_seq TO portal_api" in sql
    assert "GRANT USAGE, SELECT ON SEQUENCE platform_messages_id_seq TO portal_api" in sql


def test_platform_message_post_deploy_enforces_asymmetric_write_policies():
    sql = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "post_deploy_m1n2o3p4q5r6_platform_message_threads_rls.sql"
    ).read_text()

    assert "ALTER TABLE platform_message_threads FORCE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE platform_message_participants FORCE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE platform_messages FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY platform_message_threads_insert" in sql
    assert "WITH CHECK (current_setting('app.cross_org_admin', true) = 'true')" in sql
    assert "sender_type = 'user'" in sql
    assert "sender_user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')" in sql


def test_platform_message_startup_rls_guard_is_wired():
    database_src = (Path(__file__).resolve().parents[1] / "app" / "core" / "database.py").read_text()
    main_src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()

    assert "def assert_platform_messages_rls_ready" in database_src
    assert "platform_message_threads_select" in database_src
    assert "platform_messages_insert" in database_src
    assert "relforcerowsecurity" in database_src
    assert "await assert_platform_messages_rls_ready()" in main_src


class _RlsResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _RlsConn:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, *_args, **_kwargs):
        return _RlsResult(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _RlsEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _RlsConn(self._rows)


def _rls_rows(*, force_messages: bool = True):
    return [
        SimpleNamespace(
            relname="platform_message_threads",
            relrowsecurity=True,
            relforcerowsecurity=True,
            policies=[
                "platform_message_threads_select",
                "platform_message_threads_insert",
                "platform_message_threads_update",
                "platform_message_threads_delete",
            ],
        ),
        SimpleNamespace(
            relname="platform_message_participants",
            relrowsecurity=True,
            relforcerowsecurity=True,
            policies=[
                "platform_message_participants_select",
                "platform_message_participants_insert",
                "platform_message_participants_update",
                "platform_message_participants_delete",
            ],
        ),
        SimpleNamespace(
            relname="platform_messages",
            relrowsecurity=True,
            relforcerowsecurity=force_messages,
            policies=[
                "platform_messages_select",
                "platform_messages_insert",
                "platform_messages_update",
                "platform_messages_delete",
            ],
        ),
    ]


@pytest.mark.asyncio
async def test_assert_platform_messages_rls_ready_passes_when_all_tables_forced(monkeypatch):
    monkeypatch.setattr(db_module, "engine", _RlsEngine(_rls_rows()))

    await db_module.assert_platform_messages_rls_ready()


@pytest.mark.asyncio
async def test_assert_platform_messages_rls_ready_raises_when_force_rls_missing(monkeypatch):
    monkeypatch.setattr(db_module, "engine", _RlsEngine(_rls_rows(force_messages=False)))

    with pytest.raises(RuntimeError, match="FORCE ROW LEVEL SECURITY"):
        await db_module.assert_platform_messages_rls_ready()
