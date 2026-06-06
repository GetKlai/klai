from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.admin import platform_messages


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
    query = platform_messages._thread_select().outerjoin(
        platform_messages.PlatformMessageParticipant,
        platform_messages.PlatformMessageParticipant.thread_id
        == platform_messages.PlatformMessageThread.id,
    ).where(
        platform_messages.PlatformMessageParticipant.recipient_display_name.ilike("%jelle%")
    )

    compiled = str(query.compile(compile_kwargs={"literal_binds": True}))

    assert "platform_message_participants" in compiled
    assert "SELECT count(platform_message_participants.user_id)" in compiled


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
        calls["kwargs"] = kwargs
        return SimpleNamespace(id=2)

    async def fake_detail(db, thread_id):
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
