from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.admin import platform
from app.klai_feedback import service as feedback_service

REPO_ROOT = Path(__file__).resolve().parents[2]


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return _Result([])

    def __iter__(self):
        return iter(self._rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.params = None
        self.closed = False
        self.flushed = False
        self.refreshed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.closed = True
        return None

    async def execute(self, _query, params=None):
        if params is not None:
            self.params = params
        return _Result(self.rows)

    async def commit(self):
        return None

    async def flush(self):
        self.flushed = True

    async def refresh(self, obj):
        self.refreshed.append(obj)

    async def delete(self, _obj):
        return None


def _feedback_item(**overrides):
    now = datetime(2026, 5, 27, 10, 0, tzinfo=UTC)
    values = {
        "id": 456,
        "kind": "feature",
        "title": "Betere triage",
        "summary": "Bundel dubbele feedback.",
        "status": "open",
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
        "resolution_summary": None,
        "resolved_at": None,
        "resolved_by": None,
        "notification_state": "not_needed",
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_feedback_submission_delete_rls_policy_exists():
    migration = (
        REPO_ROOT / "backend" / "alembic" / "versions" / "c8d9e0f1a2b3_add_feedback_submission_delete_policy.py"
    ).read_text()

    assert "CREATE POLICY feedback_submissions_delete" in migration
    assert "FOR DELETE" in migration
    assert "app.cross_org_admin" in migration


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
            user_email="ada@acme.test",
            user_display_name="Ada Acme",
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
    assert result[0].user_email == "ada@acme.test"
    assert result[0].user_display_name == "Ada Acme"
    assert result[0].raw_text == "Maak het makkelijker om feedback te geven."
    assert result[0].feedback_type == "improvement"
    assert result[0].route_id == "/app/knowledge"
    assert result[0].triage_suggestion is None


@pytest.mark.asyncio
async def test_platform_feedback_submissions_includes_ai_suggestion(monkeypatch):
    created_at = datetime(2026, 5, 27, 10, 0, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            id=123,
            org_id=42,
            org_name="Acme",
            org_slug="acme",
            user_id="user-123",
            user_email="ada@acme.test",
            user_display_name="Ada Acme",
            source="assistant_feedback",
            status="new",
            raw_text="Ik wil meerdere kennisbanken tegelijk selecteren.",
            feedback_type="improvement",
            severity=None,
            page_url="https://acme.getklai.com/admin/platform",
            route_id="/admin/platform",
            locale="nl",
            viewport="1440x900",
            created_at=created_at,
        )
    ]
    session = _Session(rows)

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_suggestions(_db, submission_ids):
        assert submission_ids == [123]
        return {
            123: platform.PlatformFeedbackTriageSuggestion(
                classification="feature",
                summary="Meerdere kennisbanken selecteren.",
                suggested_area="knowledge",
                suggested_severity="medium",
                suggested_action="create_item",
                duplicate_candidates=[
                    platform.PlatformFeedbackDuplicateCandidate(
                        item_id=456,
                        confidence=0.82,
                        reason="Vergelijkbaar verzoek",
                        title="Multi-KB chat",
                        kind="feature",
                        status="open",
                        area="chat",
                    )
                ],
                model="test-model:feedback-triage-v1",
                created_at=created_at,
            )
        }

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "_platform_feedback_triage_suggestions", fake_suggestions)

    result = await platform.platform_feedback_submissions(
        search=None,
        status_filter="open",
        kind=None,
        limit=100,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    suggestion = result[0].triage_suggestion
    assert suggestion is not None
    assert suggestion.summary == "Meerdere kennisbanken selecteren."
    assert suggestion.suggested_action == "create_item"
    assert suggestion.duplicate_candidates[0].title == "Multi-KB chat"


@pytest.mark.asyncio
async def test_platform_feedback_submission_detail_returns_one_submission(monkeypatch):
    created_at = datetime(2026, 5, 27, 10, 0, tzinfo=UTC)
    rows = [
        SimpleNamespace(
            id=123,
            org_id=42,
            org_name="Acme",
            org_slug="acme",
            user_id="user-123",
            user_email="ada@acme.test",
            user_display_name="Ada Acme",
            source="assistant_problem",
            status="open",
            raw_text="BT ticket instructies veranderen opmaak.",
            feedback_type="bug",
            severity="medium",
            page_url="https://acme.getklai.com/admin/platform",
            route_id="/admin/platform",
            locale="nl",
            viewport="1440x900",
            created_at=created_at,
        )
    ]
    session = _Session(rows)

    async def fake_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)

    result = await platform.platform_feedback_submission_detail(
        submission_id=123,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert session.params == {"submission_id": 123}
    assert result.id == 123
    assert result.event_type == "klai_assistant.problem_report"
    assert result.status == "open"
    assert result.raw_text == "BT ticket instructies veranderen opmaak."


@pytest.mark.asyncio
async def test_platform_feedback_submissions_status_filter_uses_simple_status(monkeypatch):
    session = _Session([])

    async def fake_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)

    result = await platform.platform_feedback_submissions(
        search=None,
        status_filter="open",
        kind=None,
        limit=100,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result == []
    assert session.params == {"limit": 100, "status": "open"}


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
async def test_platform_feedback_update_submission_edits_text_and_status(monkeypatch):
    session = _Session([])

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_update_submission(db, submission_id, values):
        assert db is session
        assert submission_id == 123
        assert values == {
            "raw_text": "Gecorrigeerde melding",
            "status": "open",
        }
        return _SessionBound(session, id=123, status="open")

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "update_feedback_submission", fake_update_submission)

    result = await platform.platform_feedback_update_submission(
        submission_id=123,
        body=platform.PlatformFeedbackSubmissionPatchIn(
            raw_text="Gecorrigeerde melding",
            status="open",
        ),
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result.ok is True
    assert result.submission_id == 123
    assert result.status == "open"


@pytest.mark.asyncio
async def test_platform_feedback_delete_submission_deletes_evidence(monkeypatch):
    session = _Session([])
    called = {}

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_delete_submission(db, submission_id):
        assert db is session
        called["submission_id"] = submission_id

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "delete_feedback_submission", fake_delete_submission)

    result = await platform.platform_feedback_delete_submission(
        submission_id=123,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result is None
    assert called == {"submission_id": 123}


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
            _SessionBound(session, id=123, status="open"),
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

    assert result.status == "open"
    assert result.item_id == 456


@pytest.mark.asyncio
async def test_platform_feedback_items_materializes_before_session_closes(monkeypatch):
    session = _Session([])

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_search_items(db, **kwargs):
        assert db is session
        assert kwargs == {"search": None, "status": "active", "kind": "all", "limit": 25}
        return [_SessionBound(session, **_feedback_item().__dict__)]

    async def fake_reporter_orgs(db, item_ids):
        assert db is session
        assert item_ids == [456]
        return {
            456: [
                platform.PlatformFeedbackReporterOrg(
                    org_id=42,
                    org_name="Acme",
                    org_slug="acme",
                    user_count=1,
                )
            ]
        }

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "search_feedback_items", fake_search_items)
    monkeypatch.setattr(platform, "_platform_feedback_item_reporter_orgs", fake_reporter_orgs)

    result = await platform.platform_feedback_items(
        search=None,
        status="active",
        kind="all",
        limit=25,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert session.closed is True
    assert len(result) == 1
    assert result[0].id == 456
    assert result[0].title == "Betere triage"
    assert result[0].reporter_orgs[0].org_name == "Acme"


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
            user_email="ada@acme.test",
            user_display_name="Ada Acme",
            source="assistant_feedback",
            status="open",
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

    async def fake_reporter_orgs(db, item_ids):
        assert db is session
        assert item_ids == [456]
        return {}

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "get_feedback_item", fake_get_item)
    monkeypatch.setattr(platform, "_platform_feedback_item_reporter_orgs", fake_reporter_orgs)

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
            "status": "resolved",
            "owner": "Maaike",
            "target_window": "Q3",
            "external_tracker_url": "https://github.com/getklai/klai/issues/123",
            "public_feedback_url": None,
        }
        return _SessionBound(
            session,
            **_feedback_item(
                status="resolved",
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
            status="resolved",
            owner="Maaike",
            target_window="Q3",
            external_tracker_url="https://github.com/getklai/klai/issues/123",
            public_feedback_url="",
        ),
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result.status == "resolved"
    assert result.owner == "Maaike"
    assert result.target_window == "Q3"


@pytest.mark.asyncio
async def test_platform_feedback_delete_item_deletes_roadmap_item(monkeypatch):
    session = _Session([])
    called = {}

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_delete_item(db, item_id):
        assert db is session
        called["item_id"] = item_id

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "delete_feedback_item", fake_delete_item)

    result = await platform.platform_feedback_delete_item(
        item_id=456,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert result is None
    assert called == {"item_id": 456}


@pytest.mark.asyncio
async def test_platform_feedback_resolve_item_materializes_response_before_session_closes(monkeypatch):
    session = _Session([])
    notification_created_at = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)

    async def fake_audit(*_args, **_kwargs):
        return None

    async def fake_resolve_item(db, item_id, **kwargs):
        assert db is session
        assert item_id == 456
        assert kwargs == {
            "resolution_summary": "Dit is opgelost.",
            "resolved_by": "staff",
            "channels": ["in_app"],
            "subject": None,
        }
        return (
            _SessionBound(
                session,
                **_feedback_item(
                    kind="bug",
                    status="resolved",
                    resolution_summary="Dit is opgelost.",
                    resolved_by="staff",
                    notification_state="sent",
                ).__dict__,
            ),
            [
                _SessionBound(
                    session,
                    id=789,
                    item_id=456,
                    submission_id=123,
                    org_id=42,
                    user_id="user-123",
                    recipient_email=None,
                    channel="in_app",
                    status="sent",
                    subject="Bug opgelost",
                    body="Dit is opgelost.",
                    sent_at=notification_created_at,
                    read_at=None,
                    created_at=notification_created_at,
                )
            ],
        )

    monkeypatch.setattr(platform, "_audit", fake_audit)
    monkeypatch.setattr(platform, "cross_org_session", lambda: session)
    monkeypatch.setattr(platform, "resolve_feedback_item", fake_resolve_item)

    result = await platform.platform_feedback_resolve_item(
        item_id=456,
        body=platform.PlatformFeedbackResolveIn(resolution_summary="Dit is opgelost.", channels=["in_app"]),
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert session.closed is True
    assert result.item.status == "resolved"
    assert result.item.notification_state == "sent"
    assert len(result.notifications) == 1
    assert result.notifications[0].status == "sent"


@pytest.mark.asyncio
async def test_update_feedback_item_sets_shipped_at_when_resolved(monkeypatch):
    session = _Session([])
    item = _feedback_item(status="open", shipped_at=None)

    async def fake_get_item(db, item_id):
        assert db is session
        assert item_id == 456
        return item

    monkeypatch.setattr(feedback_service, "get_feedback_item", fake_get_item)

    result = await feedback_service.update_feedback_item(
        session,
        456,
        {"status": "resolved"},
    )

    assert result.status == "resolved"
    assert result.shipped_at is not None


@pytest.mark.asyncio
async def test_resolve_feedback_item_returns_snapshots_without_refresh_after_commit(monkeypatch):
    session = _Session([])
    item = _feedback_item(kind="bug", status="open")

    async def fake_get_item(db, item_id):
        assert db is session
        assert item_id == 456
        return item

    monkeypatch.setattr(feedback_service, "get_feedback_item", fake_get_item)

    result, notifications = await feedback_service.resolve_feedback_item(
        session,
        456,
        resolution_summary="Gefixt.",
        resolved_by="staff",
        channels=[],
    )

    assert result is not item
    assert result.id == item.id
    assert notifications == []
    assert item.status == "resolved"
    assert item.notification_state == "not_needed"
    assert session.flushed is True
    assert session.refreshed == []
