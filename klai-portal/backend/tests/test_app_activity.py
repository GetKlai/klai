"""SPEC-KNOWLEDGE-ACTIVITY-001 Appendix A — /api/app/activity router contract.

The org-scoped knowledge-side review surface. Repo style is DB-mocked (see
tests/test_conversation_quality_endpoints.py): ``FakeSession`` answers each
query the router issues by SQL marker and records every call, so the
assertions cover the endpoint's own decisions — which conversations qualify
for the worklist, who may see the visitor, what the server snapshots on a
review — and not SQL execution inside Postgres.

The gate tests go through ASGI so the real router-level dependencies
(``require_capability`` + both ``require_platform_unlocked``) are evaluated;
only the capability resolver behind them is substituted with the role's
PROFILE_CAPABILITIES, which is the table the gate documents.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.app_activity import router
from app.api.dependencies import get_current_user_id
from app.core.database import get_db
from app.core.permissions import get_caller
from app.core.profiles import PROFILE_CAPABILITIES
from tests.conftest import make_perms

WIDGET_UUID = "5112f9ad-3768-4b76-9a13-5b74e9165bc3"
CALLER_PORTAL_USER_ID = 77
CALLER_DISPLAY_NAME = "Klaas Klai"
T0 = dt.datetime(2026, 9, 14, 9, 12, tzinfo=dt.UTC)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def all(self) -> list[Any]:
        return self._rows

    def first(self) -> Any | None:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> Any | None:
        return self._rows[0] if self._rows else None


class FakeSession:
    """Answers the router's queries by SQL marker and records the calls."""

    def __init__(
        self,
        *,
        conversations: list[Any] | None = None,
        turns: list[Any] | None = None,
        judges: list[Any] | None = None,
        reviews: list[Any] | None = None,
        conversation: Any | None = None,
        messages: list[Any] | None = None,
        message_reviews: list[Any] | None = None,
        message: Any | None = None,
        judged: Any | None = None,
        reviewed_returning: Any | None = None,
        widget_exists: bool = True,
        summary_total: Any | None = None,
        summary_by_band: list[Any] | None = None,
        summary_by_judge_outcome: list[Any] | None = None,
        summary_by_judge_category: list[Any] | None = None,
        summary_modes: Any | None = None,
        summary_by_language: list[Any] | None = None,
        question: Any | None = None,
        review_gap_id: int | None = None,
        open_gaps: list[Any] | None = None,
        nearest_kb_slug: str | None = None,
    ) -> None:
        self.question = question
        self.nearest_kb_slug = nearest_kb_slug
        self.review_gap_id = review_gap_id
        self.open_gaps = open_gaps or []
        self.conversations = conversations or []
        self.turns = turns or []
        self.judges = judges or []
        self.reviews = reviews or []
        self.conversation = conversation
        self.messages = messages or []
        self.message_reviews = message_reviews or []
        self.message = message
        self.judged = judged
        self.reviewed_returning = reviewed_returning
        self.widget_exists = widget_exists
        self.summary_total = summary_total
        self.summary_by_band = summary_by_band or []
        self.summary_by_judge_outcome = summary_by_judge_outcome or []
        self.summary_by_judge_category = summary_by_judge_category or []
        self.summary_modes = summary_modes
        self.summary_by_language = summary_by_language or []
        # (normalised sql, params, original statement) per execute() call.
        self.calls: list[tuple[str, dict[str, Any], Any]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params or {}, statement))
        if (review_rows := self._review_write_rows(sql)) is not None:
            return review_rows
        if "SELECT 1 FROM widgets" in sql:
            return _Rows([SimpleNamespace(one=1)] if self.widget_exists else [])
        if "c.id = :conversation_id" in sql:
            # The real SQL filters is_preview=false; a fake preview row must
            # behave the same way (test_detail_404_for_a_preview_conversation).
            visible = self.conversation is not None and not self.conversation.is_preview
            return _Rows([self.conversation] if visible else [])
        # Most specific markers first: the message probe also joins
        # widget_conversations, and the detail review read also names
        # answer_reviews and portal_users.
        if "m.id = :message_id" in sql:
            return _Rows([self.message] if self.message else [])
        if "widget_conversations c" in sql:
            return _Rows(self.conversations)
        if "FROM widget_messages" in sql and "ORDER BY sequence ASC" in sql:
            return _Rows(self.messages)
        if "GROUP BY m.conversation_id" in sql:
            return _Rows(self.turns)
        if "FROM conversation_quality_judgments" in sql:
            if ":conversation_id" in sql:
                return _Rows([self.judged] if self.judged else [])
            return _Rows(self.judges)
        if "r.message_id = ANY" in sql:
            return _Rows(self.message_reviews)
        if "FROM portal_users" in sql:
            return _Rows([SimpleNamespace(id=CALLER_PORTAL_USER_ID, display_name=CALLER_DISPLAY_NAME)])
        summary = self._summary_rows(sql)
        if summary is not None:
            return summary
        if "FROM answer_reviews" in sql:
            return _Rows(self.reviews)
        raise AssertionError(f"FakeSession got unexpected SQL:\n{sql}")

    def _summary_rows(self, sql: str) -> _Rows | None:
        """GET /summary markers (most specific first): every one of them also
        matches the generic "FROM answer_reviews" fallback in execute()."""
        if "LEFT JOIN widget_messages wm" in sql:
            return _Rows([self.summary_modes] if self.summary_modes else [])
        if "GROUP BY ar.band_at_review" in sql:
            return _Rows(self.summary_by_band)
        if "GROUP BY ar.judge_outcome_at_review" in sql:
            return _Rows(self.summary_by_judge_outcome)
        if "GROUP BY ar.judge_failure_category_at_review, ar.cause" in sql:
            return _Rows(self.summary_by_judge_category)
        if "GROUP BY ar.language" in sql:
            return _Rows(self.summary_by_language)
        if sql.startswith("SELECT COUNT(*) AS reviewed"):
            return _Rows([self.summary_total] if self.summary_total else [])

    def _review_write_rows(self, sql: str) -> _Rows | None:
        """Review writes, the phase 2 gap link statements, and the test-mark
        writes (SELECT/UPDATE widget_conversations for PUT .../test)."""
        if sql.startswith("SELECT id FROM widget_conversations"):
            return _Rows([self.conversation] if self.conversation else [])
        if sql.startswith("UPDATE widget_conversations"):
            return _Rows([])
        if sql.startswith("INSERT INTO answer_reviews"):
            return _Rows([self.reviewed_returning or SimpleNamespace(reviewed_at=T0)])
        if sql.startswith("DELETE FROM answer_reviews"):
            return _Rows([])
        if sql.startswith("UPDATE answer_reviews") or sql.startswith("UPDATE portal_retrieval_gaps"):
            return _Rows([])
        if "JOIN widget_kb_access" in sql:
            return _Rows([SimpleNamespace(slug=self.nearest_kb_slug)] if self.nearest_kb_slug else [])
        if "role = 'user' AND sequence < :sequence" in sql:
            return _Rows([self.question] if self.question else [])
        if sql.startswith("SELECT gap_id FROM answer_reviews"):
            return _Rows([SimpleNamespace(gap_id=self.review_gap_id)])
        if "FROM portal_retrieval_gaps" in sql:
            return _Rows(self.open_gaps)
        return None

    async def commit(self) -> None:
        self.commits += 1

    def statements_starting_with(self, prefix: str) -> list[Any]:
        return [stmt for sql, _params, stmt in self.calls if sql.startswith(prefix)]

    def params_for(self, prefix: str) -> list[dict[str, Any]]:
        return [params for sql, params, _stmt in self.calls if sql.startswith(prefix)]

    def sql_containing(self, needle: str) -> list[str]:
        return [sql for sql, _params, _stmt in self.calls if needle in sql]


def _conv(
    cid: int,
    *,
    started_at: dt.datetime = T0,
    language: str | None = "nl",
    first_query: str | None = "Hoe koppel ik Salesforce?",
    is_preview: bool = False,
    is_test: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid,
        widget_id=uuid.UUID(WIDGET_UUID),  # asyncpg hands the uuid column back as UUID, not str
        widget_name="Voys help",
        started_at=started_at,
        last_message_at=started_at + dt.timedelta(minutes=3),
        message_count=6,
        first_user_query=first_query,
        language_detected=language,
        visitor_name=None,
        visitor_email=None,
        is_preview=is_preview,
        is_test=is_test,
    )


def _turn(cid: int, *, up: int = 0, down: int = 0, band_rank: int | None = None, refused: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        conversation_id=cid,
        ratings_up=up,
        ratings_down=down,
        worst_band_rank=band_rank,
        refused_turns=refused,
    )


def _judge(cid: int, outcome: str, category: str | None = "retrieval_miss") -> SimpleNamespace:
    return SimpleNamespace(conversation_id=cid, outcome=outcome, failure_category=category, confidence="high")


def _review(
    cid: int,
    message_id: int,
    verdict: str = "wrong",
    cause: str = "knowledge_missing",
) -> SimpleNamespace:
    return SimpleNamespace(conversation_id=cid, message_id=message_id, verdict=verdict, cause=cause)


def _message(
    mid: int,
    *,
    role: str = "assistant",
    sequence: int = 2,
    signals: dict[str, Any] | None = None,
    rating: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        role=role,
        content="Je koppelt Salesforce via de connectorpagina.",
        sources=[{"label": "Koppelingen", "title": "Koppelingen", "url": "https://help.example.com/k"}],
        created_at=T0,
        sequence=sequence,
        rating=rating,
        answer_signals=signals,
    )


# ---------------------------------------------------------------------------
# HTTP harness
# ---------------------------------------------------------------------------


def _perms(
    role: str = "kb_manager",
    *,
    unlocks: tuple[str, ...] = ("widgets", "knowledge_activity"),
    org_id: int = 101,
) -> Any:
    return make_perms(role=role, user_id="uid-test", org_id=org_id, platform_unlocked_features=list(unlocks))


async def _call(db: Any, perms: Any, method: str, url: str, json: dict[str, Any] | None = None):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: perms.user_id
    app.dependency_overrides[get_caller] = lambda: perms
    app.dependency_overrides[get_db] = lambda: db
    with pytest.MonkeyPatch.context() as monkeypatch:

        async def _role_capabilities(user_id: str, session: Any) -> set[str]:
            return set(PROFILE_CAPABILITIES[str(perms.effective_role)])

        monkeypatch.setattr("app.api.dependencies.get_effective_capabilities", _role_capabilities)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method.upper(), url, json=json)


# ---------------------------------------------------------------------------
# Gate: capability + both platform unlocks (Appendix A, router-broad)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("personal", 403),
        ("company", 403),
        ("kb_manager", 200),
        ("group_manager", 200),
        ("admin", 200),
    ],
)
async def test_conversations_gate_follows_kb_activity_capability(role: str, expected: int) -> None:
    db = FakeSession()
    response = await _call(db, _perms(role), "get", "/api/app/activity/conversations")
    assert response.status_code == expected
    if expected == 200:
        assert response.json() == {"items": [], "next_cursor": None}


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["widgets", "knowledge_activity"])
async def test_conversations_blocked_when_a_required_unlock_is_missing(missing: str) -> None:
    """AC — the unlock gate holds even for admin, the strongest role."""
    unlocks = tuple(f for f in ("widgets", "knowledge_activity") if f != missing)
    db = FakeSession()
    response = await _call(db, _perms("admin", unlocks=unlocks), "get", "/api/app/activity/conversations")
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /conversations — worklist predicate and filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_true_keeps_only_unreviewed_trouble() -> None:
    """Appendix A: onbeoordeeld én (judge ≠ resolved óf thumbsDown óf band
    low/unknown óf geweigerde beurt).

    1: resolved, no thumbsDown, band high, unreviewed  -> out
    2: thumbsDown on a turn                            -> in
    3: already reviewed, otherwise queue-worthy        -> out
    """
    db = FakeSession(
        conversations=[_conv(1), _conv(2, started_at=T0 - dt.timedelta(minutes=1)), _conv(3)],
        turns=[
            _turn(1, band_rank=3),
            _turn(2, down=1, band_rank=3),
            _turn(3, band_rank=0, refused=1),
        ],
        judges=[_judge(1, "resolved"), _judge(2, "resolved"), _judge(3, "unresolved")],
        reviews=[_review(3, 9003)],
    )
    response = await _call(db, _perms("admin"), "get", "/api/app/activity/conversations?queue=true")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [2]


@pytest.mark.asyncio
async def test_list_returns_the_contract_shape() -> None:
    db = FakeSession(
        conversations=[_conv(255)],
        turns=[_turn(255, down=1, band_rank=0)],
        judges=[_judge(255, "unresolved", "retrieval_miss")],
    )
    response = await _call(db, _perms("admin"), "get", "/api/app/activity/conversations?sort=worst")

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "id": 255,
            "widget_id": WIDGET_UUID,
            "widget_name": "Voys help",
            "channel": "webchat",
            "started_at": "2026-09-14T09:12:00Z",
            "last_message_at": "2026-09-14T09:15:00Z",
            "message_count": 6,
            "first_user_query": "Hoe koppel ik Salesforce?",
            "language": "nl",
            "worst_band": "low",
            "judge": {"outcome": "unresolved", "failure_category": "retrieval_miss", "confidence": "high"},
            "ratings": {"up": 0, "down": 1},
            "review": {"status": "unreviewed", "worst_verdict": None, "causes": []},
            "open_gap_count": 0,
        }
    ]


@pytest.mark.asyncio
async def test_sort_worst_returns_no_cursor_because_a_page_is_not_a_window() -> None:
    """``sort=worst`` re-sorts the whole candidate set in memory, so the
    ``started_at`` of the last page row is not the boundary of what is left.

    A cursor built from it asks the next call for ``started_at <`` that value
    and silently skips the conversations that are still unvisited: conv 1 is
    the newest row and the third-worst, so it disappears from every page.
    ``sort=newest`` keeps paging by cursor.
    """
    db = FakeSession(
        conversations=[
            _conv(1),
            _conv(2, started_at=T0 - dt.timedelta(minutes=1)),
            _conv(3, started_at=T0 - dt.timedelta(minutes=2)),
        ],
        turns=[
            _turn(1, band_rank=3),
            _turn(2, down=1, band_rank=3),
            _turn(3, down=1, band_rank=0),
        ],
        judges=[_judge(1, "resolved"), _judge(2, "unresolved"), _judge(3, "unresolved")],
    )

    worst = await _call(db, _perms("admin"), "get", "/api/app/activity/conversations?sort=worst&limit=2")
    assert worst.status_code == 200
    assert [item["id"] for item in worst.json()["items"]] == [3, 2]
    assert worst.json()["next_cursor"] is None

    newest = await _call(db, _perms("admin"), "get", "/api/app/activity/conversations?sort=newest&limit=2")
    assert newest.status_code == 200
    assert [item["id"] for item in newest.json()["items"]] == [1, 2]
    assert newest.json()["next_cursor"] == "2026-09-14T09:11:00Z"


@pytest.mark.asyncio
async def test_list_is_empty_for_a_widget_of_another_org() -> None:
    """Appendix A: foreign widget_id gives an empty list, never a 404."""
    db = FakeSession(widget_exists=False, conversations=[_conv(255)])
    response = await _call(db, _perms("admin"), "get", f"/api/app/activity/conversations?widget_id={WIDGET_UUID}")

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None}
    # The org-scoped probe is what decides, and the window query never runs.
    probe = db.sql_containing("SELECT 1 FROM widgets")
    assert len(probe) == 1
    assert "org_id = :org_id" in probe[0]
    assert not db.sql_containing("widget_conversations c")


@pytest.mark.asyncio
async def test_list_and_detail_exclude_preview_and_test_conversations_from_the_window() -> None:
    """The list is the review worklist and must never surface a preview or a
    test-marked conversation — both flags gate the same window query."""
    db = FakeSession(conversations=[_conv(255)])
    await _call(db, _perms("admin"), "get", "/api/app/activity/conversations")

    list_sql = db.sql_containing("ORDER BY c.started_at DESC")
    assert list_sql and "c.is_preview = false" in list_sql[0] and "c.is_test = false" in list_sql[0]


@pytest.mark.asyncio
async def test_detail_404_for_a_preview_conversation() -> None:
    """Restored origin/main behaviour: an admin preview session is not a
    conversation a reviewer can open, so the detail route 404s exactly like
    it did before the test-mark rework."""
    db = FakeSession(conversation=_conv(255, is_preview=True))
    response = await _call(db, _perms("admin"), "get", "/api/app/activity/conversations/255")

    assert response.status_code == 404
    detail_sql = db.sql_containing("c.id = :conversation_id")
    assert detail_sql and "is_preview = false" in detail_sql[0]


@pytest.mark.asyncio
async def test_detail_stays_reachable_and_carries_is_test_when_marked() -> None:
    """A reviewer who just marked a conversation as a test message still
    needs the detail route to see (and undo) that state — is_test, unlike
    is_preview, is never a 404 predicate on the detail route."""
    db = FakeSession(conversation=_conv(255, is_test=True), messages=[])
    detail = await _call(db, _perms("admin"), "get", "/api/app/activity/conversations/255")

    assert detail.status_code == 200
    assert detail.json()["is_test"] is True


# ---------------------------------------------------------------------------
# GET /conversations/{id} — transcript, visitor visibility, org scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "visitor_expected"),
    [("kb_manager", False), ("group_manager", False), ("admin", True)],
)
async def test_visitor_key_follows_admin_role(role: str, visitor_expected: bool) -> None:
    db = FakeSession(
        conversation=_conv(255),
        messages=[_message(9001, role="user", sequence=1, signals=None)],
    )
    db.conversation.visitor_name = "Ada"
    db.conversation.visitor_email = "ada@klant.nl"

    response = await _call(db, _perms(role), "get", "/api/app/activity/conversations/255")

    assert response.status_code == 200
    body = response.json()
    assert ("visitor" in body) is visitor_expected
    if visitor_expected:
        assert body["visitor"] == {"name": "Ada", "email": "ada@klant.nl"}


@pytest.mark.asyncio
async def test_detail_404_for_conversation_outside_the_callers_org() -> None:
    db = FakeSession()  # the org-scoped lookup finds nothing
    response = await _call(db, _perms("admin"), "get", "/api/app/activity/conversations/255")

    assert response.status_code == 404
    lookup = db.sql_containing("c.id = :conversation_id")
    assert len(lookup) == 1
    assert "c.org_id = :org_id" in lookup[0]
    assert db.calls[0][1] == {"conversation_id": 255, "org_id": 101}


@pytest.mark.asyncio
async def test_detail_carries_signals_judge_and_review() -> None:
    signals = {"top_score": 0.18, "band": "low", "gap_type": "soft", "sources_count": 1}
    db = FakeSession(
        conversation=_conv(255),
        messages=[
            _message(9001, role="user", sequence=1),
            _message(9002, sequence=2, signals=signals, rating="thumbsDown"),
        ],
        judges=[],
        message_reviews=[
            SimpleNamespace(
                message_id=9002,
                verdict="wrong",
                cause="knowledge_missing",
                note="KB mist het nieuwe plan",
                kb_slug="voys-help",
                reviewer_name="Klaas Klai",
                reviewed_at=T0,
            )
        ],
    )
    response = await _call(db, _perms("admin"), "get", "/api/app/activity/conversations/255")

    assert response.status_code == 200
    body = response.json()
    assert body["quality"] is None
    user_turn, assistant_turn = body["messages"]
    assert user_turn["review"] is None and user_turn["answer_signals"] is None
    assert assistant_turn["answer_signals"] == signals
    assert assistant_turn["rating"] == "thumbsDown"
    assert assistant_turn["review"] == {
        "verdict": "wrong",
        "cause": "knowledge_missing",
        "note": "KB mist het nieuwe plan",
        "kb_slug": "voys-help",
        "reviewer_name": "Klaas Klai",
        "reviewed_at": "2026-09-14T09:12:00Z",
    }


@pytest.mark.asyncio
async def test_detail_carries_the_judgment_when_present() -> None:
    """REQ-3 sidecar on the detail (SPEC-CHAT-QUALITY-LOOP-001): a judged
    conversation returns every judgment field; the null-without-judgment
    branch is covered by test_detail_carries_signals_judge_and_review."""
    db = FakeSession(
        conversation=_conv(255),
        messages=[_message(9002, sequence=2)],
        judged=SimpleNamespace(
            outcome="escalated",
            failure_category="retrieval_miss",
            reasoning="De bot kende het actuele prijsplan niet.",
            confidence="high",
            suggested_action="Prijs-KB opnieuw in de index opnemen.",
            judged_at=T0,
        ),
    )
    response = await _call(db, _perms("admin"), "get", "/api/app/activity/conversations/255")

    assert response.status_code == 200
    assert response.json()["quality"] == {
        "outcome": "escalated",
        "failure_category": "retrieval_miss",
        "reasoning": "De bot kende het actuele prijsplan niet.",
        "confidence": "high",
        "suggested_action": "Prijs-KB opnieuw in de index opnemen.",
        "judged_at": "2026-09-14T09:12:00Z",
    }


# ---------------------------------------------------------------------------
# PUT /messages/{message_id}/review
# ---------------------------------------------------------------------------


def _assistant_message_row(**over: Any) -> SimpleNamespace:
    row: dict[str, Any] = {
        "id": 9002,
        "conversation_id": 255,
        "role": "assistant",
        "sequence": 2,
        "answer_signals": {"band": "low", "top_score": 0.18, "refused": False},
        "org_id": 101,
        "language_detected": "nl",
        "is_preview": False,
        "is_test": False,
    }
    row.update(over)
    return SimpleNamespace(**row)


@pytest.mark.asyncio
async def test_put_review_rejects_wrong_verdict_with_cause_none() -> None:
    db = FakeSession(message=_assistant_message_row())
    response = await _call(
        db, _perms("admin"), "put", "/api/app/activity/messages/9002/review", json=_review_body(verdict="wrong")
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_review_snapshots_server_side_values_and_ignores_client_fields() -> None:
    db = FakeSession(
        message=_assistant_message_row(),
        judged=SimpleNamespace(outcome="unresolved", failure_category="retrieval_miss"),
    )
    response = await _call(
        db,
        _perms("admin"),
        "put",
        "/api/app/activity/messages/9002/review",
        # reviewer_user_id and the snapshots are not client-supplied at all;
        # kb_slug ("voys-help") is a client value too — the backend derives
        # its own from the widget (§4.5) and never reads the request's — so
        # both it and the extra key below must be ignored, not trusted.
        json={**_review_body(band_at_review="high", reviewer_user_id=1, turn_sequence=99), "unknown_field": "x"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "verdict": "correct",
        "cause": "none",
        "note": "Klopt",
        "kb_slug": None,
        "reviewer_name": "Klaas Klai",
        "reviewed_at": "2026-09-14T09:12:00Z",
    }
    params = db.statements_starting_with("INSERT INTO answer_reviews")[-1].compile().params
    assert params["band_at_review"] == "low"  # from answer_signals.band
    assert params["kb_slug"] is None  # no widget_kb_access row in this fake session
    assert params["reviewer_user_id"] == CALLER_PORTAL_USER_ID  # the caller, not the body
    assert params["judge_outcome_at_review"] == "unresolved"
    assert params["judge_failure_category_at_review"] == "retrieval_miss"
    assert params["language"] == "nl"
    assert params["turn_sequence"] == 2
    assert params["conversation_id"] == 255
    assert params["channel"] == "webchat"
    assert params["org_id"] == 101
    assert params["gap_id"] is None
    assert db.commits == 1


@pytest.mark.asyncio
async def test_review_kb_slug_is_derived_from_the_widget_not_the_client() -> None:
    """SPEC-KNOWLEDGE-ACTIVITY-001 §4.5: the backend decides the KB. A client
    sending a slug (the removed frontend picker's old shape) must not
    override the one derived from widget_kb_access."""
    db = FakeSession(message=_assistant_message_row(), nearest_kb_slug="voys-help")
    response = await _call(
        db,
        _perms("admin"),
        "put",
        "/api/app/activity/messages/9002/review",
        json=_review_body(kb_slug="a-client-guess"),
    )

    assert response.status_code == 200
    assert response.json()["kb_slug"] == "voys-help"


@pytest.mark.asyncio
async def test_put_review_twice_overwrites_the_same_row() -> None:
    db = FakeSession(message=_assistant_message_row())
    first = await _call(db, _perms("admin"), "put", "/api/app/activity/messages/9002/review", json=_review_body())
    second = await _call(
        db, _perms("admin"), "put", "/api/app/activity/messages/9002/review", json=_review_body(note="toegelicht")
    )

    assert first.status_code == second.status_code == 200
    upserts = db.statements_starting_with("INSERT INTO answer_reviews")
    assert len(upserts) == 2
    sql = " ".join(str(upserts[-1]).split())
    assert "ON CONFLICT (message_id)" in sql
    assert "DO UPDATE" in sql
    assert upserts[-1].compile().params["note"] == "toegelicht"


@pytest.mark.asyncio
async def test_put_review_rejects_a_user_message() -> None:
    db = FakeSession(message=_assistant_message_row(role="user"))
    response = await _call(db, _perms("admin"), "put", "/api/app/activity/messages/9001/review", json=_review_body())

    assert response.status_code == 422
    assert not db.statements_starting_with("INSERT INTO answer_reviews")


@pytest.mark.asyncio
async def test_put_review_404_for_message_of_another_org_or_preview() -> None:
    for over in ({"org_id": 999}, {"is_preview": True}):
        db = FakeSession(message=_assistant_message_row(**over))
        response = await _call(
            db, _perms("admin"), "put", "/api/app/activity/messages/9002/review", json=_review_body()
        )
        assert response.status_code == 404, over
        assert not db.statements_starting_with("INSERT INTO answer_reviews")


@pytest.mark.asyncio
async def test_put_review_422_for_a_message_in_a_test_conversation() -> None:
    """A conversation a reviewer marked as a test message cannot itself also
    be reviewed — reviewing and test-marking are mutually exclusive states."""
    db = FakeSession(message=_assistant_message_row(is_test=True))
    response = await _call(db, _perms("admin"), "put", "/api/app/activity/messages/9002/review", json=_review_body())

    assert response.status_code == 422
    assert not db.statements_starting_with("INSERT INTO answer_reviews")


def _review_body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"verdict": "correct", "cause": "none", "note": "Klopt", "kb_slug": "voys-help"}
    body.update(over)
    return body


# ---------------------------------------------------------------------------
# DELETE /messages/{message_id}/review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_review_returns_204_and_detail_shows_no_review() -> None:
    db = FakeSession(message=_assistant_message_row())
    deleted = await _call(db, _perms("admin"), "delete", "/api/app/activity/messages/9002/review")

    assert deleted.status_code == 204
    assert db.params_for("DELETE FROM answer_reviews")[-1] == {"message_id": 9002, "org_id": 101}

    detail_db = FakeSession(conversation=_conv(255), messages=[_message(9002)])
    detail = await _call(detail_db, _perms("admin"), "get", "/api/app/activity/conversations/255")
    assert detail.status_code == 200
    assert detail.json()["messages"][0]["review"] is None


# ---------------------------------------------------------------------------
# GET /queue-count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queue_count_counts_the_same_set_as_queue_true() -> None:
    conversations = [_conv(1), _conv(2), _conv(3)]
    turns = [_turn(1, band_rank=3), _turn(2, down=1, band_rank=3), _turn(3, band_rank=0, refused=1)]
    judges = [_judge(1, "resolved"), _judge(2, "resolved"), _judge(3, "unresolved")]
    reviews = [_review(3, 9003)]

    counted = await _call(
        FakeSession(conversations=conversations, turns=turns, judges=judges, reviews=reviews),
        _perms("admin"),
        "get",
        "/api/app/activity/queue-count",
    )
    listed = await _call(
        FakeSession(conversations=conversations, turns=turns, judges=judges, reviews=reviews),
        _perms("admin"),
        "get",
        "/api/app/activity/conversations?queue=true",
    )

    assert counted.status_code == 200
    assert counted.json() == {"count": 1}
    assert counted.json()["count"] == len(listed.json()["items"])


# ---------------------------------------------------------------------------
# GET /summary — calibration readout (§4.6/§4.7, Appendix A)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("role", "expected"), [("kb_manager", 200), ("company", 403)])
async def test_summary_gate_follows_kb_activity_capability(role: str, expected: int) -> None:
    """Same router-wide gate as the rest of Appendix A — no route-specific check needed."""
    response = await _call(FakeSession(), _perms(role), "get", "/api/app/activity/summary")
    assert response.status_code == expected


@pytest.mark.asyncio
async def test_summary_computes_the_documented_aggregates() -> None:
    """Appendix A ``GET /summary`` shape, built from a fixed set of fake rows
    so every field is checked against a hand-computed expectation."""
    db = FakeSession(
        summary_total=SimpleNamespace(reviewed=84),
        summary_by_band=[
            SimpleNamespace(band="high", reviewed=40, correct=34),
            SimpleNamespace(band="low", reviewed=10, correct=3),
        ],
        summary_by_judge_outcome=[
            SimpleNamespace(judge_outcome="resolved", reviewed=30, human_correct=27),
            SimpleNamespace(judge_outcome=None, reviewed=5, human_correct=2),
        ],
        summary_by_judge_category=[
            SimpleNamespace(judge_category="retrieval_miss", human_cause="knowledge_missing", row_count=12),
            SimpleNamespace(judge_category=None, human_cause="none", row_count=8),
        ],
        summary_modes=SimpleNamespace(
            broad_mode_reviewed=9,
            broad_mode_correct=5,
            strict_gap_reviewed=20,
            strict_gap_correct=7,
        ),
        summary_by_language=[
            SimpleNamespace(language="nl", reviewed=70, correct=55),
            SimpleNamespace(language="en", reviewed=14, correct=9),
        ],
    )
    response = await _call(db, _perms("kb_manager"), "get", "/api/app/activity/summary?days=30")

    assert response.status_code == 200
    assert response.json() == {
        "reviewed": 84,
        "by_band": [
            {"band": "high", "reviewed": 40, "correct": 34},
            {"band": "low", "reviewed": 10, "correct": 3},
        ],
        "by_judge_outcome": [
            {"judge_outcome": "resolved", "reviewed": 30, "human_correct": 27},
            {"judge_outcome": None, "reviewed": 5, "human_correct": 2},
        ],
        "by_judge_category": [
            {"judge_category": "retrieval_miss", "human_cause": "knowledge_missing", "count": 12},
            {"judge_category": None, "human_cause": "none", "count": 8},
        ],
        "broad_mode": {"reviewed": 9, "correct": 5},
        "strict_on_gap": {"reviewed": 20, "correct": 7},
        "by_language": [
            {"language": "nl", "reviewed": 70, "correct": 55},
            {"language": "en", "reviewed": 14, "correct": 9},
        ],
    }
    assert db.params_for("SELECT COUNT(*) AS reviewed")[-1]["org_id"] == 101
    assert all(params["org_id"] == 101 for _sql, params, _stmt in db.calls)


@pytest.mark.asyncio
async def test_summary_sql_excludes_reviews_of_test_conversations() -> None:
    """Test-mark contract: every summary bucket must drop reviews of a
    conversation with is_test=true, but keep a review whose conversation
    was purged (no widget_conversations row at all) — COALESCE(..., false)."""
    db = FakeSession()
    response = await _call(db, _perms("kb_manager"), "get", "/api/app/activity/summary")

    assert response.status_code == 200
    summary_sql = db.sql_containing("FROM answer_reviews ar")
    assert len(summary_sql) == 6  # total, band, judge_outcome, judge_category, language, modes
    for sql in summary_sql:
        assert "LEFT JOIN widget_conversations wc ON wc.id = ar.conversation_id" in sql
        assert "COALESCE(wc.is_test, false) = false" in sql


def _gap_result(gap_id: int | None = 77) -> Any:
    from app.services.gap_events import GapEventResult

    return GapEventResult("created", 101, gap_id)


async def _put_with_gap_mock(db: FakeSession, body: dict[str, Any], result: Any = None, raises: bool = False):
    """PUT a review with record_gap_event replaced; returns (response, mock)."""
    from unittest.mock import AsyncMock, patch

    mock = AsyncMock(return_value=result if result is not None else _gap_result())
    if raises:
        mock.side_effect = RuntimeError("gap write failed")
    with patch("app.api.app_activity.record_gap_event", mock):
        response = await _call(db, _perms("kb_manager"), "put", "/api/app/activity/messages/9002/review", json=body)
    return response, mock


@pytest.mark.asyncio
async def test_knowledge_missing_review_files_a_hard_gap_under_the_visitor_question() -> None:
    db = FakeSession(
        message=_assistant_message_row(),
        question=SimpleNamespace(content="Hoe koppel ik Salesforce?"),
        nearest_kb_slug="voys-help",
    )
    response, mock = await _put_with_gap_mock(db, _review_body(verdict="wrong", cause="knowledge_missing"))

    assert response.status_code == 200
    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    assert kwargs["query_text"] == "Hoe koppel ik Salesforce?"
    assert kwargs["gap_type"] == "hard"
    assert kwargs["caller_client_id"] == "human-review"
    assert kwargs["conversation_id"] == 255
    assert kwargs["language"] == "nl"
    assert kwargs["nearest_kb_slug"] == "voys-help"  # derived from the widget's KB, not the request
    link = db.params_for("UPDATE answer_reviews SET gap_id")
    assert link == [{"message_id": 9002, "org_id": 101, "gap_id": 77}]


@pytest.mark.asyncio
async def test_knowledge_wrong_review_files_a_soft_gap() -> None:
    db = FakeSession(message=_assistant_message_row(), question=SimpleNamespace(content="Wat kost Freedom?"))
    _response, mock = await _put_with_gap_mock(db, _review_body(verdict="wrong", cause="knowledge_wrong"))

    assert mock.await_args.kwargs["gap_type"] == "soft"


@pytest.mark.asyncio
async def test_re_review_with_a_knowledge_cause_reuses_the_existing_gap() -> None:
    db = FakeSession(
        message=_assistant_message_row(),
        question=SimpleNamespace(content="Wat kost Freedom?"),
        reviewed_returning=SimpleNamespace(reviewed_at=T0, gap_id=77),
    )
    _response, mock = await _put_with_gap_mock(db, _review_body(verdict="incomplete", cause="knowledge_wrong"))

    mock.assert_not_awaited()
    assert db.params_for("UPDATE answer_reviews SET gap_id") == []


@pytest.mark.asyncio
async def test_changing_the_cause_away_from_knowledge_resolves_the_linked_gap() -> None:
    db = FakeSession(
        message=_assistant_message_row(),
        reviewed_returning=SimpleNamespace(reviewed_at=T0, gap_id=77),
    )
    response, mock = await _put_with_gap_mock(db, _review_body(verdict="correct", cause="none"))

    assert response.status_code == 200
    mock.assert_not_awaited()
    assert db.params_for("UPDATE portal_retrieval_gaps SET resolved_at") == [
        {"gap_id": 77, "org_id": 101, "resolved_by": "review", "resolved_by_user_id": CALLER_PORTAL_USER_ID}
    ]
    assert db.params_for("UPDATE answer_reviews SET gap_id") == [{"message_id": 9002, "org_id": 101, "gap_id": None}]


@pytest.mark.asyncio
async def test_gap_write_failure_keeps_the_review_and_leaves_gap_id_null() -> None:
    db = FakeSession(message=_assistant_message_row(), question=SimpleNamespace(content="Wat kost Freedom?"))
    response, _mock = await _put_with_gap_mock(
        db, _review_body(verdict="wrong", cause="knowledge_missing"), raises=True
    )

    assert response.status_code == 200
    assert db.params_for("UPDATE answer_reviews SET gap_id") == []
    assert db.commits >= 1


@pytest.mark.asyncio
async def test_delete_review_resolves_the_gap_it_opened() -> None:
    db = FakeSession(review_gap_id=77)
    response = await _call(db, _perms("kb_manager"), "delete", "/api/app/activity/messages/9002/review")

    assert response.status_code == 204
    assert db.params_for("UPDATE portal_retrieval_gaps SET resolved_at") == [
        {"gap_id": 77, "org_id": 101, "resolved_by": "review", "resolved_by_user_id": CALLER_PORTAL_USER_ID}
    ]
    assert db.params_for("DELETE FROM answer_reviews") == [{"message_id": 9002, "org_id": 101}]


@pytest.mark.asyncio
async def test_list_counts_open_gaps_per_conversation() -> None:
    db = FakeSession(
        conversations=[_conv(255)],
        open_gaps=[SimpleNamespace(conversation_id=255, open_gaps=2)],
    )
    response = await _call(db, _perms("kb_manager"), "get", "/api/app/activity/conversations?queue=false")

    assert response.status_code == 200
    assert response.json()["items"][0]["open_gap_count"] == 2
    assert db.params_for("SELECT conversation_id, COUNT(*) AS open_gaps")[0]["org_id"] == 101


@pytest.mark.asyncio
async def test_re_review_with_another_knowledge_cause_retypes_the_gap() -> None:
    db = FakeSession(
        message=_assistant_message_row(),
        reviewed_returning=SimpleNamespace(reviewed_at=T0, gap_id=77),
    )
    _response, mock = await _put_with_gap_mock(db, _review_body(verdict="wrong", cause="knowledge_wrong"))

    mock.assert_not_awaited()
    assert db.params_for("UPDATE portal_retrieval_gaps SET gap_type") == [
        {"gap_id": 77, "org_id": 101, "gap_type": "soft"}
    ]


@pytest.mark.asyncio
async def test_review_language_falls_back_to_the_answer_signal() -> None:
    """Conversations written before the widget path stored the question
    language have language_detected NULL; the answer signal carries it."""
    db = FakeSession(
        message=_assistant_message_row(language_detected=None, answer_signals={"band": "low", "language": "en"}),
        question=SimpleNamespace(content="How do I reset my password?"),
    )
    _response, mock = await _put_with_gap_mock(db, _review_body(verdict="wrong", cause="knowledge_missing"))

    assert mock.await_args.kwargs["language"] == "en"
    params = db.statements_starting_with("INSERT INTO answer_reviews")[-1].compile().params
    assert params["language"] == "en"


# ---------------------------------------------------------------------------
# PUT /conversations/{id}/test — mark/unmark a reviewer test conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_conversation_as_test_resolves_open_gaps_with_the_caller_id() -> None:
    db = FakeSession(conversation=_conv(255))
    response = await _call(
        db, _perms("admin"), "put", "/api/app/activity/conversations/255/test", json={"is_test": True}
    )

    assert response.status_code == 200
    assert response.json() == {"is_test": True}
    assert db.params_for("UPDATE widget_conversations") == [{"conversation_id": 255, "org_id": 101, "is_test": True}]
    assert db.params_for("UPDATE portal_retrieval_gaps SET resolved_at = NOW(), resolved_by = 'test'") == [
        {"conversation_id": 255, "org_id": 101, "resolved_by_user_id": CALLER_PORTAL_USER_ID}
    ]
    assert db.params_for("UPDATE portal_retrieval_gaps SET resolved_at = NULL") == []
    assert db.commits == 1


@pytest.mark.asyncio
async def test_unmark_conversation_as_test_reopens_only_the_rows_it_resolved() -> None:
    db = FakeSession(conversation=_conv(255, is_test=True))
    response = await _call(
        db, _perms("admin"), "put", "/api/app/activity/conversations/255/test", json={"is_test": False}
    )

    assert response.status_code == 200
    assert response.json() == {"is_test": False}
    assert db.params_for("UPDATE widget_conversations") == [{"conversation_id": 255, "org_id": 101, "is_test": False}]
    assert db.params_for("UPDATE portal_retrieval_gaps SET resolved_at = NOW(), resolved_by = 'test'") == []
    assert db.params_for("UPDATE portal_retrieval_gaps SET resolved_at = NULL") == [
        {"conversation_id": 255, "org_id": 101}
    ]


@pytest.mark.asyncio
async def test_mark_conversation_as_test_404_for_another_org() -> None:
    db = FakeSession()  # no conversation row -> the org-scoped probe finds nothing
    response = await _call(
        db, _perms("admin"), "put", "/api/app/activity/conversations/255/test", json={"is_test": True}
    )

    assert response.status_code == 404
    assert db.params_for("UPDATE widget_conversations") == []
