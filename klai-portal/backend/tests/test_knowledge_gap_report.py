"""The weekly knowledge-gap list from stored chat signals (app/services/knowledge_gap_report.py).

synthetic-data: generator=tests/test_knowledge_gap_report.py::_signals seed=20261028
Every answer_signals dict below is built by ``_signals`` from fictional
products, questions and example.com URLs; the key-stability test draws its
wordings from ``random.Random(SEED)``. No value comes from a real tenant.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import knowledge_gap_report as report
from app.services.knowledge_gap_report import GapTurn, build_clusters, cluster_key, load_gap_turns

SEED = 20261028
_T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _signals(
    *,
    decision: str = "answer",
    gap_type: str | None = None,
    product: str = "Wolkcentrale",
    problem: str = "",
    query: str = "Hoe zet ik wachtmuziek aan op de wolkcentrale?",
    found: list[dict] | None = None,
    open_parts: list[str] | None = None,
    language: str = "nl",
) -> dict[str, Any]:
    if found is None:
        found = [{"title": "Wachtmuziek instellen", "url": "https://help.example.com/wachtmuziek", "score": 0.82}]
    return {
        "decision": decision,
        "gap_type": gap_type,
        "language": language,
        "case": {"problem": problem, "product": product},
        "search": {"query": query, "variants": [], "found": found},
        "selection": {"verdict": "answers", "parts": [], "open": open_parts or [], "question": ""},
    }


def _widget(minutes: int = 0, **kwargs: Any) -> GapTurn:
    return GapTurn("widget", _signals(**kwargs), _T0 + timedelta(minutes=minutes))


def _only(clusters: list[dict]) -> dict:
    assert len(clusters) == 1
    return clusters[0]


def test_counts_the_four_gap_decisions_and_leaves_plain_answers_out() -> None:
    turns = [
        _widget(decision=decision, problem=f"{word} instellen op de centrale")
        for decision, word in (
            ("refusal", "nachtstand"),
            ("clarifying_question", "belgroep"),
            ("human_request", "factuuradres"),
            ("partial_answer", "voicemail"),
            ("answer", "doorschakelen"),
            ("off_topic", "weerbericht"),
        )
    ]

    clusters = build_clusters(turns, {})

    assert sorted(c["topic"] for c in clusters) == sorted(
        " ".join(sorted([w, "centrale", "instellen"])) for w in ("nachtstand", "belgroep", "factuuradres", "voicemail")
    )
    assert all(c["count"] == 1 for c in clusters)


def test_an_answer_on_a_weak_search_counts_as_weak_match() -> None:
    cluster = _only(build_clusters([_widget(decision="answer", gap_type="soft")], {}))

    assert cluster["kind"] == "weak_match"
    assert cluster["article"] == {
        "title": "Wachtmuziek instellen",
        "url": "https://help.example.com/wachtmuziek",
        "score": 0.82,
    }


def test_a_hard_gap_is_no_article() -> None:
    cluster = _only(build_clusters([_widget(decision="refusal", gap_type="hard", found=[])], {}))

    assert cluster["kind"] == "no_article"
    assert cluster["article"] is None


def test_open_parts_next_to_a_found_article_is_article_misses_part() -> None:
    cluster = _only(build_clusters([_widget(decision="partial_answer", open_parts=["wachtmuziek na 18 uur"])], {}))

    assert cluster["kind"] == "article_misses_part"
    assert cluster["open_parts"] == ["wachtmuziek na 18 uur"]
    assert cluster["article"]["url"] == "https://help.example.com/wachtmuziek"
    assert cluster["question"] == "Hoe zet ik wachtmuziek aan op de wolkcentrale?"


def test_a_clarifying_question_is_variant_unclear() -> None:
    assert _only(build_clusters([_widget(decision="clarifying_question")], {}))["kind"] == "variant_unclear"


def test_a_request_for_a_person_is_a_contact_wish() -> None:
    assert _only(build_clusters([_widget(decision="human_request")], {}))["kind"] == "contact_wish"


def test_a_cluster_of_one_turn_stays_listed_at_the_bottom() -> None:
    turns = [
        _widget(minutes=5, decision="refusal", query="Kan ik een faxnummer koppelen?", found=[], problem=""),
        *(_widget(minutes=m, decision="refusal", problem="wachtmuziek instellen op de centrale") for m in (1, 2, 3)),
    ]

    clusters = build_clusters(turns, {})

    assert [c["count"] for c in clusters] == [3, 1]
    assert clusters[-1]["question"] == "Kan ik een faxnummer koppelen?"


def test_internal_turns_count_per_kind_without_text() -> None:
    turns = [
        GapTurn("internal", {"decision": "refusal", "gap_type": "hard"}, _T0),
        GapTurn("internal", {"decision": "refusal", "gap_type": "hard"}, _T0),
        GapTurn("internal", {"decision": "answer", "gap_type": "soft"}, _T0),
    ]

    clusters = build_clusters(turns, {})

    assert [(c["cluster_key"], c["count"], c["internal_count"], c["question"]) for c in clusters] == [
        ("internal|no_article", 2, 2, None),
        ("internal|weak_match", 1, 1, None),
    ]


def test_a_decided_cluster_leaves_the_list_and_shows_with_include_decided() -> None:
    turns = [_widget(decision="refusal", problem="wachtmuziek instellen")]
    key = cluster_key(turns[0])
    decisions = {
        key: {"decision": "out_of_scope", "decided_by": "Fictief Persoon", "decided_at": "2026-10-21T10:00:00+00:00"}
    }

    assert build_clusters(turns, decisions) == []
    shown = _only(build_clusters(turns, decisions, include_decided=True))
    assert (shown["decision"], shown["decided_by"]) == ("out_of_scope", "Fictief Persoon")


def test_the_cluster_key_is_the_same_for_the_same_topic_in_another_wording_and_week() -> None:
    """Product plus the three longest content words, sorted: word order, case,
    punctuation, filler words and the date do not move a turn to another key."""
    rng = random.Random(SEED)  # noqa: S311 -- a seeded, reproducible test generator, not a secret
    # The product's own word is already the key's first part, so it never takes a topic slot.
    words = ["wachtmuziek", "instellen", "kantooruren", "wolkcentrale"]
    fillers = ["hoe", "kan", "ik", "de", "na", "graag", "?", "!", ","]
    keys = set()
    for week in range(4):
        wording = [*words, *rng.sample(fillers, 4)]
        rng.shuffle(wording)
        text = " ".join(w.upper() if rng.random() < 0.3 else w for w in wording)
        turn = GapTurn("widget", _signals(decision="refusal", problem=text), _T0 + timedelta(weeks=week))
        keys.add(cluster_key(turn))

    assert keys == {"wolkcentrale|instellen kantooruren wachtmuziek"}


def _row(signals: dict, *, origin: str | None = "https://shop.example.com", first: str = "Vraag") -> SimpleNamespace:
    return SimpleNamespace(answer_signals=signals, created_at=_T0, loaded_origin=origin, first_user_query=first)


class _Db:
    """Answers the widget query with ``widget_rows`` and the internal one with nothing; records the SQL."""

    def __init__(self, widget_rows: list[SimpleNamespace]) -> None:
        self.widget_rows = widget_rows
        self.compiled: list[Any] = []

    async def execute(self, stmt: Any) -> MagicMock:
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.compiled.append(compiled)
        result = MagicMock()
        result.all.return_value = self.widget_rows if "widget_messages" in str(compiled) else []
        return result


@pytest.mark.asyncio
async def test_the_widget_monitoring_probe_is_not_a_gap() -> None:
    probe = _signals(decision="refusal", query=report.PROBE_QUESTION)
    db = _Db(
        [
            # Either mark is enough: the origin, or the fixed question from another origin.
            _row(probe, origin=report.PROBE_ORIGIN, first="Hoe lang bakt het Proefbrood?"),
            _row(probe, origin=None, first=report.PROBE_QUESTION),
            _row(_signals(decision="refusal")),
        ]
    )

    turns = await load_gap_turns(db, org_id=7, since=_T0)  # type: ignore[arg-type]

    assert [t.signals["search"]["query"] for t in turns] == ["Hoe zet ik wachtmuziek aan op de wolkcentrale?"]


@pytest.mark.asyncio
async def test_every_read_is_bound_to_the_one_org() -> None:
    db = _Db([])

    await load_gap_turns(db, org_id=7, since=_T0)  # type: ignore[arg-type]

    widget_sql, internal_sql = (str(c) for c in db.compiled)
    assert "widget_messages.org_id = " in widget_sql
    assert "widget_conversations.org_id = " in widget_sql
    assert "internal_chat_turns.org_id = " in internal_sql
    for compiled in db.compiled:
        assert [v for k, v in compiled.params.items() if k.startswith("org_id")] == [7] * (
            2 if "widget_messages" in str(compiled) else 1
        )
