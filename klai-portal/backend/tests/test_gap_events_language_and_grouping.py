"""SPEC-KNOWLEDGE-ACTIVITY-001 §4.5 / §4.9: gap rows and the grouping judge.

A gap row remembers which conversation it came from and in which language the
question was asked, and every row gets a ``question_key`` that the grouping
judge can fold onto an earlier paraphrase.

Harness note: real-DB tests are opt-in through ``RLS_TEST_DATABASE_URL``, so
the contract that lives in SQL is asserted on the compiled statement.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.services.gap_events import record_gap_event
from app.services.support_cases import _question_key

_NOW = datetime(2026, 9, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# record_gap_event — conversation + language on the write path
# ---------------------------------------------------------------------------


class _FakeOrg:
    def __init__(self, telemetry_level: str = "full") -> None:
        self.id = 42
        self.zitadel_org_id = "zit-org-1"
        self.telemetry_level = telemetry_level


def _scalar_result(value: object) -> MagicMock:
    res = MagicMock()
    res.scalar_one_or_none.return_value = value
    return res


@pytest.mark.asyncio
async def test_record_gap_event_stores_conversation_and_language(monkeypatch) -> None:
    """§4.5: a gap written with conversation/language keeps both; the widget
    pad of deel 1 writes neither, so both must stay NULL."""
    monkeypatch.setattr("app.services.gap_events.set_tenant", AsyncMock())
    rows: list[Any] = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(_FakeOrg("full")))
    db.add = MagicMock(side_effect=rows.append)

    result = await record_gap_event(
        db,
        zitadel_org_id="zit-org-1",
        user_id="u-1",
        query_text="Waar vind ik het retourbeleid?",
        gap_type="hard",
        conversation_id=1234,
        language="nl",
    )
    await record_gap_event(
        db,
        zitadel_org_id="zit-org-1",
        user_id="u-1",
        query_text="Waar vind ik het retourbeleid?",
        gap_type="soft",
    )

    assert result.outcome == "created"
    assert len(rows) == 2
    assert rows[0].conversation_id == 1234
    assert rows[0].language == "nl"
    assert rows[1].conversation_id is None
    assert rows[1].language is None


@pytest.mark.asyncio
async def test_a_late_filed_gap_is_dated_by_its_conversation(monkeypatch) -> None:
    """A verdict filed weeks after its conversation (the judge backfill) must
    land on the conversation's date: the inbox counts needs per 30 days, and
    dating them all today would pile a month into one week."""
    monkeypatch.setattr("app.services.gap_events.set_tenant", AsyncMock())
    monkeypatch.setattr("app.services.gap_events.asyncio.create_task", lambda coro: coro.close())
    rows: list[Any] = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(_FakeOrg("full")))
    db.add = MagicMock(side_effect=rows.append)
    asked = datetime(2026, 9, 2, 14, 30, tzinfo=UTC)

    await record_gap_event(
        db, zitadel_org_id="zit-org-1", user_id="u-1", query_text="Belgroep?", gap_type="hard", occurred_at=asked
    )

    assert rows[0].occurred_at == asked


# ---------------------------------------------------------------------------
# record_gap_event — SPEC-RAG-GAP-GROUPING: every producer writes a
# question_key, and a paraphrase of an open group is folded onto it async.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_gap_event_writes_a_question_key(monkeypatch) -> None:
    """Every gap row now carries a question_key, so a group is a need and not a
    literal text."""
    monkeypatch.setattr("app.services.gap_events.set_tenant", AsyncMock())
    rows: list[Any] = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(_FakeOrg("full")))
    db.add = MagicMock(side_effect=rows.append)

    def _closing_create_task(coro):
        coro.close()  # never actually scheduled: avoid the GC "never awaited" warning
        return MagicMock()

    with patch("app.services.gap_events.asyncio.create_task", side_effect=_closing_create_task):
        await record_gap_event(
            db,
            zitadel_org_id="zit-org-1",
            user_id="u-1",
            query_text="How do I port my number?",
            gap_type="hard",
            nearest_kb_slug="products",
            language="en",
        )

    assert rows[0].question_key == _question_key(
        question="How do I port my number?", language="en", kb_slug="products", audience=None
    )


def _fake_tenant_scoped_session(session: AsyncMock):
    @contextlib.asynccontextmanager
    async def _session(org_id: int):
        yield session

    return _session


@pytest.mark.asyncio
async def test_record_gap_event_folds_a_paraphrase_onto_an_existing_group(monkeypatch) -> None:
    """Two differently-worded chat questions about the same need collapse into
    one group: the async grouping pass folds the second row's question_key
    onto the first row's (SPEC-RAG-GAP-GROUPING) — reusing
    ``support_gap_grouping.group_findings``, not a second mechanism."""
    monkeypatch.setattr("app.services.gap_events.set_tenant", AsyncMock())
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(_FakeOrg("full")))
    db.add = MagicMock()

    captured: list[Any] = []
    monkeypatch.setattr("app.services.gap_events.asyncio.create_task", lambda coro: captured.append(coro))

    await record_gap_event(
        db,
        zitadel_org_id="zit-org-1",
        user_id="u-1",
        query_text="How do I move my number over?",
        gap_type="hard",
        nearest_kb_slug="products",
        language="en",
    )
    grouping_coro = next(c for c in captured if c.cr_code.co_name == "_group_gap")
    for other in captured:
        if other is not grouping_coro:
            other.close()  # unused taxonomy-classification coroutine: avoid the GC warning

    existing_key = _question_key(question="How do I port my number?", language="en", kb_slug="products", audience=None)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    session.commit = AsyncMock()

    with (
        patch("app.core.database.tenant_scoped_session", _fake_tenant_scoped_session(session)),
        patch(
            "app.services.support_cases._open_group_candidates",
            AsyncMock(
                return_value=[
                    {
                        "question_key": existing_key,
                        "question": "How do I port my number?",
                        "language": "en",
                        "audience": None,
                    }
                ]
            ),
        ),
        patch(
            "app.services.support_gap_grouping.group_findings",
            AsyncMock(return_value=[{"group_question_key": existing_key}]),
        ),
    ):
        await grouping_coro

    update_stmt = session.execute.await_args.args[0]
    compiled = update_stmt.compile(dialect=postgresql.dialect())
    assert "UPDATE portal_retrieval_gaps" in str(compiled)
    assert existing_key in compiled.params.values()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_gap_event_grouping_failure_leaves_the_row_on_its_own_key(monkeypatch) -> None:
    """A grouping-judge failure (timeout, malformed model output, ...) must
    never lose the row — it already committed on its own literal key before
    this async pass ever runs; the pass just logs and stops."""
    monkeypatch.setattr("app.services.gap_events.set_tenant", AsyncMock())
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(_FakeOrg("full")))
    db.add = MagicMock()

    captured: list[Any] = []
    monkeypatch.setattr("app.services.gap_events.asyncio.create_task", lambda coro: captured.append(coro))

    await record_gap_event(
        db,
        zitadel_org_id="zit-org-1",
        user_id="u-1",
        query_text="How do I move my number over?",
        gap_type="hard",
        nearest_kb_slug="products",
        language="en",
    )
    grouping_coro = next(c for c in captured if c.cr_code.co_name == "_group_gap")
    for other in captured:
        if other is not grouping_coro:
            other.close()  # unused taxonomy-classification coroutine: avoid the GC warning

    session = AsyncMock()
    with (
        patch("app.core.database.tenant_scoped_session", _fake_tenant_scoped_session(session)),
        patch("app.services.support_cases._open_group_candidates", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        await grouping_coro  # must not raise

    session.execute.assert_not_awaited()  # no UPDATE was ever attempted


@pytest.mark.asyncio
async def test_the_grouping_judge_calls_of_a_gap_name_the_tenant(monkeypatch) -> None:
    """The fold sends the visitor's question to the model on LiteLLM's master
    key, which belongs to no tenant: without the org in the body LiteLLM's PII
    enforcer cannot mask it (klai_pii_enforce._delegated_org_id)."""
    monkeypatch.setattr("app.services.gap_events.set_tenant", AsyncMock())
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(_FakeOrg("full")))
    db.add = MagicMock()
    captured: list[Any] = []
    monkeypatch.setattr("app.services.gap_events.asyncio.create_task", lambda coro: captured.append(coro))

    await record_gap_event(
        db,
        zitadel_org_id="zit-org-1",
        user_id="u-1",
        query_text="How do I move my number over?",
        gap_type="hard",
        nearest_kb_slug="products",
        language="en",
    )
    grouping_coro = next(c for c in captured if c.cr_code.co_name == "_group_gap")
    for other in captured:
        if other is not grouping_coro:
            other.close()

    existing_key = _question_key(question="How do I port my number?", language="en", kb_slug="products", audience=None)
    candidate = {
        "question_key": existing_key,
        "question": "How do I port my number?",
        "language": "en",
        "audience": None,
    }
    reply = {"assignments": [{"index": 0, "group_question_key": existing_key}]}
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))
    with (
        respx.mock(assert_all_called=False) as router,
        patch("app.core.database.tenant_scoped_session", _fake_tenant_scoped_session(session)),
        patch("app.services.support_cases._open_group_candidates", AsyncMock(return_value=[candidate])),
    ):
        litellm = router.post(f"{settings.litellm_base_url}/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
        )
        await grouping_coro

    bodies = [json.loads(call.request.content) for call in litellm.calls]
    assert len(bodies) == 2  # the grouping call and its verification
    assert [body.get("metadata") for body in bodies] == [
        {"_klai_delegated_org_id": "zit-org-1", "tags": ["portal:gap-grouping"]}
    ] * 2


@pytest.mark.asyncio
async def test_the_grouping_judge_sees_the_most_similar_groups_not_an_arbitrary_hundred() -> None:
    """With hundreds of open groups the judge prompt cannot hold them all, and
    an unordered LIMIT handed it whichever groups the planner returned first,
    so the one it should match could simply be missing. Candidates are now
    ranked by embedding similarity to the question and only the closest go in."""
    from app.services import gap_events

    candidates = [
        {"question_key": f"k{i}", "question": f"vraag {i}", "language": "nl", "audience": None} for i in range(60)
    ]
    # The closest group of all is in another language: the judge could never
    # accept it, so it must not take a shortlist place.
    candidates.append({"question_key": "en-twin", "question": "question", "language": "en", "audience": None})

    async def _embed(texts: list[str]) -> list[list[float]]:
        # The question points along x; candidate i leans towards x as i grows.
        return [[1.0, 0.0]] + [[i / 60, 1 - i / 60] for i in range(len(texts) - 1)]

    seen: list[list[str]] = []

    async def _judge(findings: list[dict], cands: list[dict], delegated_org_id: str | None = None) -> list[dict]:
        seen.append([c["question_key"] for c in cands])
        return findings

    session = AsyncMock()
    with (
        patch("app.core.database.tenant_scoped_session", _fake_tenant_scoped_session(session)),
        patch("app.services.support_cases._open_group_candidates", AsyncMock(return_value=candidates)),
        patch("app.services.support_gap_grouping.group_findings", _judge),
        patch.object(gap_events, "_embed", _embed),
    ):
        await gap_events.fold_into_open_group(
            org_id=1, kb_slug=None, question="vraag", language="nl", audience=None, base_key="own"
        )

    assert len(seen[0]) == gap_events.PROMPT_CANDIDATES
    assert seen[0][0] == "k59"  # the most similar compatible group comes first
    assert "k0" not in seen[0]  # the least similar is left out
    assert "en-twin" not in seen[0]
