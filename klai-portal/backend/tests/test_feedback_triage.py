from types import SimpleNamespace

import pytest

from app.klai_feedback import triage


class _FakeDb:
    def __init__(self):
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def _submission(**overrides):
    values = {
        "id": 123,
        "source": "assistant_feedback",
        "raw_text": "De chat is traag op de platform pagina.",
        "status": "new",
        "route_id": "/admin/platform",
        "locale": "nl",
        "metadata_json": {"feedback_type": "improvement"},
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _item(**overrides):
    values = {
        "id": 456,
        "kind": "bug",
        "title": "Chat performance verbeteren",
        "summary": "De chat voelt soms traag.",
        "area": "assistant",
        "status": "inbox",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_generate_feedback_triage_suggestion_is_idempotent(monkeypatch):
    existing = SimpleNamespace(id=999)

    async def fake_existing(_db, submission_id, model_key):
        assert submission_id == 123
        assert model_key == "test-model:feedback-triage-v1"
        return existing

    async def classifier(*_args):
        raise AssertionError("classifier should not run when suggestion exists")

    monkeypatch.setattr(triage, "get_existing_triage_suggestion", fake_existing)

    result = await triage.generate_feedback_triage_suggestion(
        _FakeDb(),
        123,
        classifier=classifier,
        model="test-model",
    )

    assert result is existing


@pytest.mark.asyncio
async def test_generate_feedback_triage_suggestion_persists_draft(monkeypatch):
    db = _FakeDb()
    submission = _submission()
    candidates = [_item()]

    async def fake_existing(_db, _submission_id, _model_key):
        return None

    async def fake_get_submission(_db, submission_id):
        assert submission_id == 123
        return submission

    async def fake_search(_db, *, search, status, kind, limit):
        assert "chat" in search
        assert status == "triage"
        assert kind == "all"
        assert limit == 20
        return candidates

    async def fake_classifier(classifier_submission, classifier_candidates, model):
        assert classifier_submission is submission
        assert classifier_candidates == candidates
        assert model == "test-model"
        return triage.TriageSuggestionDraft(
            classification="bug",
            summary="Chat is traag op Platform.",
            suggested_area="assistant",
            suggested_severity="medium",
            duplicate_candidates=[{"item_id": 456, "confidence": 0.82, "reason": "Zelfde performance klacht"}],
            suggested_action="link_existing",
        )

    monkeypatch.setattr(triage, "get_existing_triage_suggestion", fake_existing)
    monkeypatch.setattr(triage, "get_feedback_submission", fake_get_submission)
    monkeypatch.setattr(triage, "search_feedback_items", fake_search)

    result = await triage.generate_feedback_triage_suggestion(
        db,
        123,
        classifier=fake_classifier,
        model="test-model",
    )

    assert result is db.added[0]
    assert result.submission_id == 123
    assert result.classification == "bug"
    assert result.summary == "Chat is traag op Platform."
    assert result.duplicate_candidates_json == {
        "candidates": [{"item_id": 456, "confidence": 0.82, "reason": "Zelfde performance klacht"}]
    }
    assert result.suggested_action == "link_existing"
    assert result.model == "test-model:feedback-triage-v1"
    assert submission.status == "new"
    assert db.commits == 1


def test_parse_triage_response_filters_unknown_duplicate_ids():
    parsed = triage._parse_triage_response(
        """
        {
          "classification": "bug",
          "summary": "Chat is traag.",
          "suggested_area": "assistant",
          "suggested_severity": "urgent",
          "suggested_action": "link_existing",
          "duplicate_candidates": [
            {"item_id": 456, "confidence": 0.91, "reason": "match"},
            {"item_id": 999, "confidence": 0.99, "reason": "invented"}
          ]
        }
        """,
        {456},
    )

    assert parsed.classification == "bug"
    assert parsed.suggested_severity == "urgent"
    assert parsed.duplicate_candidates == [{"item_id": 456, "confidence": 0.91, "reason": "match"}]


def test_triage_system_prompt_explicitly_handles_problem_reports():
    assert "assistant_problem" in triage._TRIAGE_SYSTEM
    assert "bug proposal" in triage._TRIAGE_SYSTEM


@pytest.mark.asyncio
async def test_run_feedback_triage_for_submission_swallows_ai_failure(monkeypatch):
    class _Ctx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_exc):
            return None

    async def fake_generate(*_args, **_kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr(triage, "cross_org_session", lambda: _Ctx())
    monkeypatch.setattr(triage, "generate_feedback_triage_suggestion", fake_generate)

    await triage.run_feedback_triage_for_submission(123)
