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
            event_type="klai_assistant.feedback",
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
    assert result[0].raw_text == "Maak het makkelijker om feedback te geven."
    assert result[0].feedback_type == "improvement"
    assert result[0].route_id == "/app/knowledge"
