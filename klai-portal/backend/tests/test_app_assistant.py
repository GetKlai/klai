from types import SimpleNamespace

import pytest

from app.api import app_assistant


def _perms():
    return SimpleNamespace(
        org_id=42,
        org_slug="acme",
        user_id="user-123",
        effective_role=SimpleNamespace(value="member"),
    )


def _request():
    return SimpleNamespace(
        headers={
            "user-agent": "pytest",
            "referer": "https://app.getklai.com/app/chat?token=secret#section",
        },
        client=SimpleNamespace(host="127.0.0.1"),
    )


@pytest.mark.asyncio
async def test_submit_feedback_emits_first_party_event(monkeypatch):
    emitted = []

    def fake_emit_event(event_type, org_id=None, user_id=None, properties=None):
        emitted.append(
            {
                "event_type": event_type,
                "org_id": org_id,
                "user_id": user_id,
                "properties": properties,
            }
        )

    monkeypatch.setattr(app_assistant, "emit_event", fake_emit_event)

    body = app_assistant.AssistantFeedbackIn(
        raw_text="Maak het makkelijker om feedback te geven.",
        page_url="https://app.getklai.com/app/chat?token=secret#section",
        route_id="/app/chat",
        type="improvement",
    )
    response = await app_assistant.submit_feedback(body, _request(), _perms())

    assert response.ok is True
    assert emitted == [
        {
            "event_type": "klai_assistant.feedback",
            "org_id": 42,
            "user_id": "user-123",
            "properties": {
                "raw_text": "Maak het makkelijker om feedback te geven.",
                "page_url": "https://app.getklai.com/app/chat",
                "route_id": "/app/chat",
                "locale": "nl",
                "viewport": None,
                "org_slug": "acme",
                "role": "member",
                "source": "klai_assistant",
                "user_agent": "pytest",
                "referer": "https://app.getklai.com/app/chat",
                "client_host": "127.0.0.1",
                "feedback_type": "improvement",
            },
        }
    ]


@pytest.mark.asyncio
async def test_submit_problem_report_emits_separate_event(monkeypatch):
    emitted = []

    def fake_emit_event(event_type, org_id=None, user_id=None, properties=None):
        emitted.append((event_type, org_id, user_id, properties))

    monkeypatch.setattr(app_assistant, "emit_event", fake_emit_event)

    body = app_assistant.AssistantProblemReportIn(
        raw_text="De pagina blijft laden.",
        page_url="https://app.getklai.com/app/knowledge",
        route_id="/app/knowledge",
        severity="blocked",
        viewport="1440x900",
    )
    response = await app_assistant.submit_problem_report(body, _request(), _perms())

    assert response.ok is True
    event_type, org_id, user_id, properties = emitted[0]
    assert event_type == "klai_assistant.problem_report"
    assert org_id == 42
    assert user_id == "user-123"
    assert properties["severity"] == "blocked"
    assert properties["source"] == "klai_assistant"
    assert properties["viewport"] == "1440x900"
