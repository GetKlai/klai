"""SPEC-KNOWLEDGE-ACTIVITY-001 §4.5 / §4.9 / Appendix B (fase 2, deel 1).

A gap row remembers which conversation it came from and in which language the
question was asked; the dashboard groups and filters per language, labels a
group's source, links back to a still-existing conversation, and a human can
close a group (the rescorer is not the only closer any more).

Harness note: there is no live-PostgreSQL lane for these endpoints (real-DB
tests are opt-in through ``RLS_TEST_DATABASE_URL``), so the grouping and
filtering contract — which lives in SQL — is asserted on the compiled
statement, the same way ``test_gap_rescorer.py`` pins grouped ordering.
Row-level response fields are asserted against the endpoint's own rows.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.app_gaps import GapResolveRequest, list_gaps, resolve_gap
from app.services.gap_events import record_gap_event
from app.services.support_cases import _question_key
from tests.conftest import make_perms

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


# ---------------------------------------------------------------------------
# record_gap_event — SPEC-RAG-GAP-GROUPING: every producer writes a
# question_key, and a paraphrase of an open group is folded onto it async.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_gap_event_writes_a_question_key(monkeypatch) -> None:
    """Every gap row now carries a question_key, so list_gaps can group chat
    rows the same way it already groups support findings."""
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


# ---------------------------------------------------------------------------
# GET /api/app/gaps — language grouping, source, conversation link
# ---------------------------------------------------------------------------


def _group_row(
    query_text: str = "retourbeleid",
    gap_type: str = "hard",
    language: str | None = None,
    *,
    group_key: str | None = None,
    has_review: bool = False,
    resolved_at: datetime | None = None,
) -> SimpleNamespace:
    # A pre-migration row has no persisted question_key, so its group_key
    # falls back to its own literal query_text (COALESCE(question_key,
    # query_text) in app_gaps.py) — the default here mirrors that fallback.
    return SimpleNamespace(
        group_key=group_key if group_key is not None else query_text,
        query_text=query_text,
        gap_type=gap_type,
        language=language,
        top_score=0.11,
        nearest_kb_slug="kb-a",
        occurrence_count=1,
        last_occurred=_NOW,
        resolved_at=resolved_at,
        has_review=has_review,
    )


def _resolved_by_row(
    query_text: str,
    gap_type: str = "hard",
    language: str | None = None,
    *,
    group_key: str | None = None,
    resolved_by: str | None = "review",
    resolved_by_name: str | None = "Klaas Klai",
) -> SimpleNamespace:
    return SimpleNamespace(
        group_key=group_key if group_key is not None else query_text,
        gap_type=gap_type,
        language=language,
        resolved_by=resolved_by,
        resolved_by_name=resolved_by_name,
    )


def _conv_row(
    query_text: str,
    conversation_id: int,
    gap_type: str = "hard",
    language: str | None = None,
    *,
    group_key: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        group_key=group_key if group_key is not None else query_text,
        gap_type=gap_type,
        language=language,
        conversation_id=conversation_id,
    )


class _FakeGapDb:
    """Mock AsyncSession for the dashboard: answers by statement kind and
    records every SQL it was handed.

    The dashboard's conversation-attribution statement is the one that touches
    ``widget_conversations``; that JOIN is also the existence check, so a
    conversation the retention job already purged simply never comes back.
    """

    def __init__(
        self,
        group_rows: list[Any],
        conversation_rows: list[Any] | None = None,
        resolved_by_rows: list[Any] | None = None,
    ) -> None:
        self.group_rows = group_rows
        self.conversation_rows = conversation_rows or []
        self.resolved_by_rows = resolved_by_rows or []
        self.compiled: list[Any] = []

    @property
    def statements(self) -> list[str]:
        return [str(c) for c in self.compiled]

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.compiled.append(compiled)
        sql = str(compiled)
        result = MagicMock()
        if "widget_conversations" in sql:
            result.all.return_value = self.conversation_rows
        elif "portal_users" in sql:
            result.all.return_value = self.resolved_by_rows
        else:
            result.all.return_value = self.group_rows
        return result


async def _list_gaps(db: Any, **overrides: Any):
    kwargs: dict[str, Any] = {
        "days": 30,
        "gap_type": None,
        "language": None,
        "taxonomy_node_id": None,
        "limit": 50,
        "include_resolved": False,
        "perms": make_perms(role="admin"),
        "db": db,
    }
    kwargs.update(overrides)
    return await list_gaps(**kwargs)


@pytest.mark.asyncio
async def test_list_gaps_groups_open_groups_per_language() -> None:
    """The same question in two languages is two gaps: grouping must include
    language, and an open-only list keeps ``resolved_at IS NULL``."""
    db = _FakeGapDb([_group_row(language="nl"), _group_row(language="en")])

    out = await _list_gaps(db)

    assert [(g.query_text, g.language) for g in out.gaps] == [
        ("retourbeleid", "nl"),
        ("retourbeleid", "en"),
    ]
    assert out.total == 2
    grouped = db.statements[0]
    assert (
        "GROUP BY coalesce(portal_retrieval_gaps.question_key, portal_retrieval_gaps.query_text), "
        "portal_retrieval_gaps.gap_type, portal_retrieval_gaps.language"
    ) in grouped
    assert "portal_retrieval_gaps.resolved_at IS NULL" in grouped


@pytest.mark.asyncio
async def test_list_gaps_language_filter_keeps_only_that_language() -> None:
    """?language=en must drop the nl group in SQL, not in Python — otherwise
    the dashboard page would silently lose the other language."""
    db = _FakeGapDb([_group_row(language="en")])

    out = await _list_gaps(db, language="en")

    assert [g.language for g in out.gaps] == ["en"]
    assert "WHERE" in db.statements[0]
    assert "portal_retrieval_gaps.language = " in db.statements[0]


@pytest.mark.asyncio
async def test_list_gaps_labels_group_source_from_caller_client_id() -> None:
    """One human-review row in a group makes the group review-sourced; a group
    nobody reviewed stays automatic."""
    db = _FakeGapDb(
        [
            _group_row(query_text="uit de review", has_review=True),
            _group_row(query_text="automatisch", has_review=False),
        ]
    )

    out = await _list_gaps(db)

    assert {g.query_text: g.source for g in out.gaps} == {
        "uit de review": "review",
        "automatisch": "automatic",
    }
    # The label is per group, so the caller check must be an aggregate — a
    # plain column would pick an arbitrary row of the group.
    grouped = db.statements[0]
    assert "bool_or(portal_retrieval_gaps.caller_client_id = " in grouped
    assert "human-review" in db.compiled[0].params.values()


@pytest.mark.asyncio
async def test_list_gaps_links_latest_live_conversation_only() -> None:
    """§4.5: the link goes to the newest conversation row of the group, and a
    conversation that no longer exists is not linked at all."""
    db = _FakeGapDb(
        [
            _group_row(query_text="met gesprek"),
            _group_row(query_text="verwijderd gesprek"),
        ],
        [
            _conv_row("met gesprek", 11),
            _conv_row("met gesprek", 7),
        ],
    )

    out = await _list_gaps(db)

    assert {g.query_text: g.conversation_id for g in out.gaps} == {
        "met gesprek": 11,
        "verwijderd gesprek": None,
    }
    attribution = db.statements[1]
    assert "JOIN widget_conversations" in attribution
    assert "portal_retrieval_gaps.conversation_id IS NOT NULL" in attribution


@pytest.mark.asyncio
async def test_list_gaps_without_rows_runs_no_attribution_query() -> None:
    db = _FakeGapDb([])

    out = await _list_gaps(db)

    assert out.gaps == []
    # No legacy rows -> the expensive widget-conversation and resolved_by
    # attribution joins are skipped. (A cheap org-telemetry PK lookup still
    # runs to decide whether case-backed support findings are visible.)
    assert not any("widget_conversations" in stmt for stmt in db.statements)
    assert not any("portal_users" in stmt for stmt in db.statements)


@pytest.mark.asyncio
async def test_list_gaps_include_resolved_carries_closer_fields() -> None:
    """A resolved group in an ``include_resolved=true`` list carries who
    closed it; an open group in the same list stays null on all three."""
    db = _FakeGapDb(
        [
            _group_row(query_text="gesloten", resolved_at=_NOW),
            _group_row(query_text="open"),
        ],
        resolved_by_rows=[_resolved_by_row("gesloten", resolved_by="review", resolved_by_name="Klaas Klai")],
    )

    out = await _list_gaps(db, include_resolved=True)

    by_query = {g.query_text: g for g in out.gaps}
    assert by_query["gesloten"].resolved_at == _NOW
    assert by_query["gesloten"].resolved_by == "review"
    assert by_query["gesloten"].resolved_by_name == "Klaas Klai"
    assert by_query["open"].resolved_by is None
    assert by_query["open"].resolved_by_name is None


@pytest.mark.asyncio
async def test_list_gaps_open_only_skips_the_resolved_by_query() -> None:
    """The default (``include_resolved=false``) list never has a resolved row
    to attribute, so it must not pay for the extra query."""
    db = _FakeGapDb([_group_row(query_text="open")])

    await _list_gaps(db)

    assert not any("portal_users" in stmt for stmt in db.statements)


# ---------------------------------------------------------------------------
# POST /api/app/gaps/resolve — the human close action
# ---------------------------------------------------------------------------


class _FakeResolveDb:
    def __init__(self, rowcount: int, caller_id: int | None = 55) -> None:
        self.rowcount = rowcount
        self.caller_id = caller_id
        self.compiled: list[Any] = []
        self.commits = 0

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> MagicMock:
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.compiled.append(compiled)
        result = MagicMock()
        if "portal_users" in str(compiled):
            result.scalar_one_or_none.return_value = self.caller_id
        else:
            result.rowcount = self.rowcount
        return result

    async def commit(self) -> None:
        self.commits += 1


def _update_stmt(db: _FakeResolveDb) -> Any:
    return next(c for c in db.compiled if "UPDATE portal_retrieval_gaps" in str(c))


@pytest.mark.asyncio
async def test_resolve_gap_closes_open_group_rows_in_own_org() -> None:
    db = _FakeResolveDb(3)

    out = await resolve_gap(
        GapResolveRequest(query_text="retourbeleid", gap_type="hard", language="nl"),
        perms=make_perms(role="admin"),
        db=db,
    )

    assert out.resolved == 3
    assert db.commits == 1
    stmt = _update_stmt(db)
    sql = str(stmt)
    assert "UPDATE portal_retrieval_gaps" in sql
    assert "portal_retrieval_gaps.resolved_at IS NULL" in sql
    assert "portal_retrieval_gaps.language = " in sql
    assert "portal_retrieval_gaps.org_id = " in sql
    assert make_perms(role="admin").org_id in stmt.params.values()


@pytest.mark.asyncio
async def test_resolve_gap_writes_manual_and_the_caller_id() -> None:
    db = _FakeResolveDb(1, caller_id=55)

    await resolve_gap(
        GapResolveRequest(query_text="retourbeleid", gap_type="hard", language="nl"),
        perms=make_perms(role="admin"),
        db=db,
    )

    params = _update_stmt(db).params
    assert params["resolved_by"] == "manual"
    assert params["resolved_by_user_id"] == 55


@pytest.mark.asyncio
async def test_resolve_gap_404s_when_nothing_open_and_matches_null_language() -> None:
    """Also covers the other-org case: the org predicate is part of the same
    WHERE, so another org's open group matches zero rows → 404."""
    db = _FakeResolveDb(0)

    with pytest.raises(HTTPException) as exc:
        await resolve_gap(
            GapResolveRequest(query_text="retourbeleid", gap_type="hard", language=None),
            perms=make_perms(role="admin"),
            db=db,
        )

    assert exc.value.status_code == 404
    assert db.commits == 0
    sql = str(_update_stmt(db))
    assert "portal_retrieval_gaps.language IS NULL" in sql
