from types import SimpleNamespace

import pytest

from app.api.admin import platform


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def execute(self, _query, params):
        self.params = params
        return _Result(self.rows)


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
        limit=100,
        perms=SimpleNamespace(org_id=1, user_id="staff"),
    )

    assert session.params == {"limit": 100, "q": "%acme%"}
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
        return SimpleNamespace(id=123, status="dismissed")

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
        return SimpleNamespace(id=123, status="linked"), SimpleNamespace(id=456)

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
