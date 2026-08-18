"""Chat-context enrichment for /app/chat feedback submissions.

Contract: feedback submitted from the chat page gets the reporter's most
recent LibreChat conversations attached to ``metadata_json["chat_context"]``
as recency-based candidates, because the cross-origin chat iframe makes the
conversation unreachable client-side. The enrichment runs as a background
task AFTER the submission is durably stored and is best-effort: it must
never delay or fail the feedback POST, and triage always runs.

All identifiers below are synthetic fixtures.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks

from app.api import app_assistant
from app.services import librechat_chat_context


def _perms():
    return SimpleNamespace(
        org_id=42,
        org_slug="acme",
        user_id="1000000000000000001",
        effective_role=SimpleNamespace(value="member"),
    )


def _request():
    return SimpleNamespace(
        headers={"user-agent": "pytest", "referer": "https://acme.getklai.com/app/chat"},
        client=SimpleNamespace(host="127.0.0.1"),
    )


class TestRecentChatConversations:
    @pytest.mark.asyncio
    async def test_maps_mongo_documents_to_metadata_entries(self, monkeypatch):
        def fake_sync(db_name, zitadel_user_id, limit):
            assert db_name == "librechat-acme"
            assert zitadel_user_id == "1000000000000000001"
            return [
                {
                    "conversationId": "00000000-0000-4000-8000-000000000001",
                    "title": "Routing question",
                    "model": "klai-primary",
                    "createdAt": datetime(2026, 1, 10, 9, 12, 29, tzinfo=UTC),
                    "updatedAt": datetime(2026, 1, 10, 11, 54, 32, tzinfo=UTC),
                }
            ]

        monkeypatch.setattr(librechat_chat_context, "_sync_recent_conversations", fake_sync)
        result = await librechat_chat_context.recent_chat_conversations("acme", "1000000000000000001")

        assert result == [
            {
                "conversation_id": "00000000-0000-4000-8000-000000000001",
                "title": "Routing question",
                "model": "klai-primary",
                "url": "https://chat-acme.getklai.com/c/00000000-0000-4000-8000-000000000001",
                "created_at": "2026-01-10T09:12:29+00:00",
                "updated_at": "2026-01-10T11:54:32+00:00",
            }
        ]

    @pytest.mark.asyncio
    async def test_truncates_long_titles_and_tolerates_missing_fields(self, monkeypatch):
        monkeypatch.setattr(
            librechat_chat_context,
            "_sync_recent_conversations",
            lambda *args: [{"conversationId": "abc", "title": "x" * 500}],
        )
        result = await librechat_chat_context.recent_chat_conversations("acme", "sub")

        assert result is not None
        assert len(result[0]["title"]) == 200
        assert result[0]["model"] is None
        assert result[0]["created_at"] is None

    @pytest.mark.asyncio
    async def test_naive_mongo_datetimes_are_normalized_to_utc(self, monkeypatch):
        # pymongo returns naive datetimes by default; the stored ISO strings
        # must still carry an explicit UTC offset.
        monkeypatch.setattr(
            librechat_chat_context,
            "_sync_recent_conversations",
            lambda *args: [
                {
                    "conversationId": "abc",
                    "createdAt": datetime(2026, 1, 10, 9, 12, 29),
                    "updatedAt": datetime(2026, 1, 10, 11, 54, 32),
                }
            ],
        )
        result = await librechat_chat_context.recent_chat_conversations("acme", "sub")

        assert result is not None
        assert result[0]["created_at"] == "2026-01-10T09:12:29+00:00"
        assert result[0]["updated_at"] == "2026-01-10T11:54:32+00:00"

    @pytest.mark.asyncio
    async def test_non_string_bson_values_are_dropped(self, monkeypatch):
        # On LibreChat schema drift a field may come back as an ObjectId or
        # embedded document; those must never reach the JSONB column where
        # they would fail serialization outside our error handling.
        class FakeObjectId:
            pass

        monkeypatch.setattr(
            librechat_chat_context,
            "_sync_recent_conversations",
            lambda *args: [
                {
                    "conversationId": FakeObjectId(),
                    "title": {"nested": "doc"},
                    "model": 42,
                }
            ],
        )
        result = await librechat_chat_context.recent_chat_conversations("acme", "sub")

        assert result == [
            {
                "conversation_id": None,
                "title": None,
                "model": None,
                "url": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_returns_none_when_reporter_has_no_librechat_user(self, monkeypatch):
        monkeypatch.setattr(librechat_chat_context, "_sync_recent_conversations", lambda *args: None)
        assert await librechat_chat_context.recent_chat_conversations("acme", "sub") is None

    @pytest.mark.asyncio
    async def test_returns_none_on_mongo_failure(self, monkeypatch):
        def boom(*args):
            raise ConnectionError("mongo unreachable")

        monkeypatch.setattr(librechat_chat_context, "_sync_recent_conversations", boom)
        assert await librechat_chat_context.recent_chat_conversations("acme", "sub") is None


class TestFollowupScheduling:
    async def _submit_problem(self, monkeypatch, *, route_id, page_url):
        async def fake_create(db, **kwargs):
            return SimpleNamespace(id=7)

        monkeypatch.setattr(app_assistant, "emit_event", lambda *a, **k: None)
        monkeypatch.setattr(app_assistant, "create_feedback_submission", fake_create)

        body = app_assistant.AssistantProblemReportIn(
            raw_text="De assistent geeft verkeerde antwoorden.",
            page_url=page_url,
            route_id=route_id,
            severity="workaround",
        )
        background_tasks = BackgroundTasks()
        response = await app_assistant.submit_problem_report(body, _request(), background_tasks, _perms(), object())
        assert response.ok is True
        return background_tasks.tasks

    @pytest.mark.asyncio
    async def test_chat_route_schedules_enrichment_then_triage(self, monkeypatch):
        tasks = await self._submit_problem(
            monkeypatch,
            route_id="/app/chat",
            page_url="https://acme.getklai.com/app/chat",
        )
        assert [t.func for t in tasks] == [app_assistant.enrich_chat_context_and_triage]
        assert tasks[0].args == (7, "acme", "1000000000000000001")

    @pytest.mark.asyncio
    async def test_non_chat_route_schedules_plain_triage(self, monkeypatch):
        tasks = await self._submit_problem(
            monkeypatch,
            route_id="/app/knowledge",
            page_url="https://acme.getklai.com/app/knowledge",
        )
        assert [t.func for t in tasks] == [app_assistant.run_feedback_triage_for_submission]
        assert tasks[0].args == (7,)

    @pytest.mark.asyncio
    async def test_feedback_endpoint_also_schedules_enrichment(self, monkeypatch):
        async def fake_create(db, **kwargs):
            return SimpleNamespace(id=8)

        monkeypatch.setattr(app_assistant, "emit_event", lambda *a, **k: None)
        monkeypatch.setattr(app_assistant, "create_feedback_submission", fake_create)

        body = app_assistant.AssistantFeedbackIn(
            raw_text="Chat geeft rare antwoorden.",
            page_url="https://acme.getklai.com/app/chat",
            route_id="/app/chat",
            type="confusing",
        )
        background_tasks = BackgroundTasks()
        response = await app_assistant.submit_feedback(body, _request(), background_tasks, _perms(), object())
        assert response.ok is True
        assert [t.func for t in background_tasks.tasks] == [app_assistant.enrich_chat_context_and_triage]


class TestEnrichChatContextAndTriage:
    def _wire(self, monkeypatch, *, conversations, submission):
        async def fake_recent(org_slug, zitadel_user_id):
            return conversations

        db = AsyncMock()

        @asynccontextmanager
        async def fake_session():
            yield db

        async def fake_get_submission(_db, submission_id):
            return submission

        triage_calls = []

        async def fake_triage(submission_id):
            triage_calls.append(submission_id)

        monkeypatch.setattr(app_assistant, "recent_chat_conversations", fake_recent)
        monkeypatch.setattr(app_assistant, "cross_org_session", fake_session)
        monkeypatch.setattr(app_assistant, "get_feedback_submission", fake_get_submission)
        monkeypatch.setattr(app_assistant, "run_feedback_triage_for_submission", fake_triage)
        return db, triage_calls

    @pytest.mark.asyncio
    async def test_attaches_conversations_and_runs_triage(self, monkeypatch):
        submission = SimpleNamespace(metadata_json={"org_slug": "acme"})
        conversations = [{"conversation_id": "abc", "title": "t"}]
        db, triage_calls = self._wire(monkeypatch, conversations=conversations, submission=submission)

        await app_assistant.enrich_chat_context_and_triage(7, "acme", "sub")

        assert submission.metadata_json == {
            "org_slug": "acme",
            "chat_context": {"recent_conversations": conversations},
        }
        db.commit.assert_awaited_once()
        assert triage_calls == [7]

    @pytest.mark.asyncio
    async def test_no_conversations_skips_write_but_runs_triage(self, monkeypatch):
        submission = SimpleNamespace(metadata_json={"org_slug": "acme"})
        db, triage_calls = self._wire(monkeypatch, conversations=None, submission=submission)

        await app_assistant.enrich_chat_context_and_triage(7, "acme", "sub")

        assert submission.metadata_json == {"org_slug": "acme"}
        db.commit.assert_not_awaited()
        assert triage_calls == [7]

    @pytest.mark.asyncio
    async def test_persist_failure_still_runs_triage(self, monkeypatch):
        submission = SimpleNamespace(metadata_json={})
        db, triage_calls = self._wire(monkeypatch, conversations=[{"conversation_id": "abc"}], submission=submission)
        db.commit.side_effect = RuntimeError("db down")

        await app_assistant.enrich_chat_context_and_triage(7, "acme", "sub")

        assert triage_calls == [7]
