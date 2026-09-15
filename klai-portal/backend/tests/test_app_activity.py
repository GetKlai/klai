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
    ) -> None:
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
        # (normalised sql, params, original statement) per execute() call.
        self.calls: list[tuple[str, dict[str, Any], Any]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = " ".join(str(statement).split())
        self.calls.append((sql, params or {}, statement))
        if sql.startswith("INSERT INTO answer_reviews"):
            return _Rows([self.reviewed_returning or SimpleNamespace(reviewed_at=T0)])
        if sql.startswith("DELETE FROM answer_reviews"):
            return _Rows([])
        if "SELECT 1 FROM widgets" in sql:
            return _Rows([SimpleNamespace(one=1)] if self.widget_exists else [])
        if "c.id = :conversation_id" in sql:
            return _Rows([self.conversation] if self.conversation else [])
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
        if "FROM answer_reviews" in sql:
            return _Rows(self.reviews)
        raise AssertionError(f"FakeSession got unexpected SQL:\n{sql}")

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
) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid,
        widget_id=WIDGET_UUID,
        widget_name="Voys help",
        started_at=started_at,
        last_message_at=started_at + dt.timedelta(minutes=3),
        message_count=6,
        first_user_query=first_query,
        language_detected=language,
        visitor_name=None,
        visitor_email=None,
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
async def test_every_read_excludes_preview_conversations() -> None:
    db = FakeSession(conversations=[_conv(255)], conversation=_conv(255), messages=[])
    await _call(db, _perms("admin"), "get", "/api/app/activity/conversations")
    await _call(db, _perms("admin"), "get", "/api/app/activity/conversations/255")

    for sql in db.sql_containing("widget_conversations c"):
        assert "is_preview = false" in sql


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
        # the extra keys below must be ignored, not trusted.
        json={**_review_body(band_at_review="high", reviewer_user_id=1, turn_sequence=99), "unknown_field": "x"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "verdict": "correct",
        "cause": "none",
        "note": "Klopt",
        "kb_slug": "voys-help",
        "reviewer_name": "Klaas Klai",
        "reviewed_at": "2026-09-14T09:12:00Z",
    }
    params = db.statements_starting_with("INSERT INTO answer_reviews")[-1].compile().params
    assert params["band_at_review"] == "low"  # from answer_signals.band
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
