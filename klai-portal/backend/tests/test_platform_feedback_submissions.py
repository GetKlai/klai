from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.api.admin import platform
from app.klai_feedback import service as feedback_service


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.params = None
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.closed = True
        return None

    async def execute(self, _query, params=None):
        self.params = params
        return _Result(self.rows)

    async def commit(self):
        return None


def _feedback_item(**overrides):
    now = datetime(2026, 5, 27, 10, 0, tzinfo=UTC)
    values = {
        "id": 456,
        "kind": "feature",
        "title": "Betere triage",
        "summary": "Bundel dubbele feedback.",
        "status": "inbox",
        "area": "platform",
        "priority_score": 12,
        "org_count": 2,
        "user_count": 3,
        "external_tracker_type": None,
        "external_tracker_id": None,
        "external_tracker_url": None,
        "public_feedback_url": None,
        "public_title": None,
        "public_summary": None,
        "target_window": None,
        "owner": None,
        "shipped_at": None,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _SessionBound:
    def __init__(self, session, **values):
        self._session = session
        self._values = values

    def __getattr__(self, name):
        if self._session.closed:
            raise AssertionError(f"{name} was read after the DB session closed")
        return self._values[name]


@pytest.mark.asyncio
async def test_platform_feedback_submissions_reads_assistant_events(monkeypatch):
    rows = [
        SimpleNamespace(
            id=123,
            org_id=42,
            org_name="Acme",
            org_slug="acme",
            user_id="user-123",
            source="assistant_feedback",
            status="new",
            raw_text="Maak het makkelijker om feedback te geven.",
            feedback_type="improvement",
            severity=None,
            page_url="https://acme.getklai.com/app/knowledge",
            route_id="/app/knowledge",
            locale="nl",
            viewport="1440x900",
            created_at="2026-05-27T10:00:00Z",
        )
    ]
    session = _Session(rows)

    async def fake_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)

    result = await platform.platform_feedback_submissions(
        search="acme",
        status_filter="new",
        kind="feedback",
        limit=100,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert session.params == {
        "limit": 100,
        "q": "%acme%",
        "status": "new",
        "source": "assistant_feedback",
    }
    assert len(result) == 1
    assert result[0].org_name == "Acme"
    assert result[0].event_type == "klai_assistant.feedback"
    assert result[0].status == "new"
    assert result[0].raw_text == "Maak het makkelijker om feedback te geven."
    assert result[0].feedback_type == "improvement"
    assert result[0].route_id == "/app/knowledge"


@pytest.mark.asyncio
async def test_platform_feedback_dismiss_updates_submission(monkeypatch):
    session = _Session([])

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_dismiss(db, submission_id):
        assert db is session
        assert submission_id == 123
        return _SessionBound(session, id=123, status="dismissed")

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "dismiss_feedback_submission", fake_dismiss)

    result = await platform.platform_feedback_dismiss_submission(
        submission_id=123,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result.ok is True
    assert result.submission_id == 123
    assert result.status == "dismissed"


@pytest.mark.asyncio
async def test_platform_feedback_create_item_links_submission(monkeypatch):
    session = _Session([])

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_create_item(db, **kwargs):
        assert db is session
        assert kwargs == {
            "submission_id": 123,
            "kind": "feature",
            "title": "Betere triage",
            "summary": "Bundel dubbele feedback.",
            "area": "platform",
            "link_type": "evidence",
        }
        return (
            _SessionBound(session, id=123, status="linked"),
            _SessionBound(session, id=456),
        )

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "create_feedback_item_from_submission", fake_create_item)

    result = await platform.platform_feedback_create_item(
        submission_id=123,
        body=platform.PlatformFeedbackCreateItemIn(
            kind="feature",
            title="Betere triage",
            summary="Bundel dubbele feedback.",
            area="platform",
        ),
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result.status == "linked"
    assert result.item_id == 456


@pytest.mark.asyncio
async def test_platform_feedback_items_materializes_before_session_closes(monkeypatch):
    session = _Session([])

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_search_items(db, **kwargs):
        assert db is session
        assert kwargs == {"search": None, "limit": 25}
        return [_SessionBound(session, **_feedback_item().__dict__)]

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "search_feedback_items", fake_search_items)

    result = await platform.platform_feedback_items(
        search=None,
        limit=25,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert session.closed is True
    assert len(result) == 1
    assert result[0].id == 456
    assert result[0].title == "Betere triage"


@pytest.mark.asyncio
async def test_platform_feedback_item_detail_returns_linked_customer_evidence(monkeypatch):
    linked_at = datetime(2026, 5, 27, 11, 0, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            id=123,
            org_id=42,
            org_name="Acme",
            org_slug="acme",
            user_id="user-123",
            source="assistant_feedback",
            status="linked",
            raw_text="Maak triage minder handmatig.",
            feedback_type="improvement",
            severity=None,
            page_url="https://acme.getklai.com/admin/platform",
            route_id="/admin/platform",
            locale="nl",
            viewport="1440x900",
            created_at=datetime(2026, 5, 27, 10, 30, tzinfo=UTC),
            link_type="evidence",
            linked_at=linked_at,
        )
    ]
    session = _Session(rows)

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_get_item(db, item_id):
        assert db is session
        assert item_id == 456
        return _SessionBound(session, **_feedback_item().__dict__)

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "get_feedback_item", fake_get_item)

    result = await platform.platform_feedback_item_detail(
        item_id=456,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result.item.id == 456
    assert result.item.title == "Betere triage"
    assert len(result.submissions) == 1
    assert result.submissions[0].org_name == "Acme"
    assert result.submissions[0].event_type == "klai_assistant.feedback"
    assert result.submissions[0].link_type == "evidence"
    assert result.submissions[0].raw_text == "Maak triage minder handmatig."


@pytest.mark.asyncio
async def test_platform_feedback_update_item_saves_roadmap_fields(monkeypatch):
    session = _Session([])

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_update_item(db, item_id, values):
        assert db is session
        assert item_id == 456
        assert values == {
            "status": "planned",
            "owner": "Maaike",
            "target_window": "Q3",
            "external_tracker_url": "https://github.com/getklai/klai/issues/123",
            "public_feedback_url": None,
        }
        return _SessionBound(
            session,
            **_feedback_item(
                status="planned",
                owner="Maaike",
                target_window="Q3",
                external_tracker_url="https://github.com/getklai/klai/issues/123",
            ).__dict__,
        )

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "update_feedback_item", fake_update_item)

    result = await platform.platform_feedback_update_item(
        item_id=456,
        body=platform.PlatformFeedbackItemPatchIn(
            status="planned",
            owner="Maaike",
            target_window="Q3",
            external_tracker_url="https://github.com/getklai/klai/issues/123",
            public_feedback_url="",
        ),
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result.status == "planned"
    assert result.owner == "Maaike"
    assert result.target_window == "Q3"


@pytest.mark.asyncio
async def test_update_feedback_item_sets_shipped_at(monkeypatch):
    session = _Session([])
    item = _feedback_item(status="planned", shipped_at=None)

    async def fake_get_item(db, item_id):
        assert db is session
        assert item_id == 456
        return item

    monkeypatch.setattr(feedback_service, "get_feedback_item", fake_get_item)

    result = await feedback_service.update_feedback_item(
        session,
        456,
        {"status": "shipped"},
    )

    assert result.status == "shipped"
    assert result.shipped_at is not None
