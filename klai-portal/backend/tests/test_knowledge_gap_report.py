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
from app.services.knowledge_gap_report import (
    GapTurn,
    build_clusters,
    cluster_key,
    knowledge_gap_clusters,
    load_gap_turns,
    topic_words,
)

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


def _widget(minutes: int = 0, visitor_text: str | None = None, **kwargs: Any) -> GapTurn:
    return GapTurn("widget", _signals(**kwargs), _T0 + timedelta(minutes=minutes), visitor_text)


def _only(clusters: list[dict]) -> dict:
    assert len(clusters) == 1
    return clusters[0]


def test_counts_the_four_gap_decisions_and_leaves_plain_answers_out() -> None:
    turns = [
        _widget(decision=decision, problem=f"{word} instellen op de centrale", found=[])
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
        " ".join(sorted([w, "centrale"])) for w in ("nachtstand", "belgroep", "factuuradres", "voicemail")
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


def test_the_question_is_the_visitors_own_message_and_the_query_only_without_one() -> None:
    """The case step rewrites the search query into the articles' language and
    strips names and numbers; the proposal asks for the visitor's own words."""
    turns = [
        _widget(
            minutes=2,
            visitor_text="hoe krijg ik muziek als mensen in de wacht staan",
            decision="refusal",
            problem="wachtmuziek",
        ),
        _widget(minutes=1, decision="refusal", problem="wachtmuziek"),
    ]

    cluster = _only(build_clusters(turns, {}))

    assert cluster["questions"] == [
        "hoe krijg ik muziek als mensen in de wacht staan",
        "Hoe zet ik wachtmuziek aan op de wolkcentrale?",
    ]


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


def test_catch_all_clusters_are_not_decidable() -> None:
    """Internal turns and turns without topic words share one key per kind or
    product; a final decision there would hide every later turn of that kind."""
    clusters = build_clusters(
        [
            GapTurn("internal", {"decision": "refusal", "gap_type": "hard"}, _T0),
            # Only filler words: the product alone is left, a catch-all for that product.
            _widget(decision="human_request", problem="", query="kan dat graag?"),
            _widget(decision="refusal", problem="wachtmuziek instellen"),
        ],
        {},
    )

    assert sorted((c["topic"], c["decidable"]) for c in clusters) == [
        ("wachtmuziek", True),
        ("wolkcentrale", False),
        ("—", False),
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
    """Product plus the three longest content words: word order, case,
    punctuation, filler words and the date do not move a turn to another key."""
    rng = random.Random(SEED)  # noqa: S311 -- a seeded, reproducible test generator, not a secret
    # The product's own word is already part of the key, so it never takes a topic slot.
    words = ["wachtmuziek", "nachtstand", "kantooruren", "wolkcentrale"]
    fillers = ["hoe", "kan", "ik", "de", "na", "graag", "?", "!", ","]
    turns = []
    for week in range(4):
        wording = [*words, *rng.sample(fillers, 4)]
        rng.shuffle(wording)
        text = " ".join(w.upper() if rng.random() < 0.3 else w for w in wording)
        turns.append(GapTurn("widget", _signals(decision="refusal", problem=text), _T0 + timedelta(weeks=week)))

    cluster = _only(build_clusters(turns, {}))

    assert (cluster["count"], cluster["topic"]) == (4, "kantooruren nachtstand wachtmuziek")
    assert "wachtmuziek" not in cluster["cluster_key"]


def test_no_number_or_address_becomes_part_of_the_topic() -> None:
    words = topic_words(
        "Mijn nummer 06-12345678 en mailadres jan.fictief@example.com, ordernummer KLT2026A, "
        "rekening NL00EXMP0123456789, doorschakelen werkt niet"
    )

    assert words == ["doorschakelen", "mailadres", "ordernummer"]


def _row(
    signals: dict, *, origin: str | None = "https://shop.example.com", first: str = "Vraag", visitor: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        answer_signals=signals, created_at=_T0, loaded_origin=origin, first_user_query=first, visitor_text=visitor
    )


class _Db:
    """Answers the widget query with ``widget_rows``, the org's level with ``level``, the rest with nothing."""

    def __init__(self, widget_rows: list[SimpleNamespace], level: str = "full") -> None:
        self.widget_rows = widget_rows
        self.level = level
        self.compiled: list[Any] = []

    async def execute(self, stmt: Any) -> MagicMock:
        compiled = stmt.compile(dialect=postgresql.dialect())
        self.compiled.append(compiled)
        sql = str(compiled)
        result = MagicMock()
        result.scalar_one.return_value = self.level
        result.all.return_value = self.widget_rows if "FROM widget_messages" in sql else []
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
async def test_every_read_is_bound_to_the_one_org_and_leaves_previews_and_tests_out() -> None:
    db = _Db([])

    await load_gap_turns(db, org_id=7, since=_T0)  # type: ignore[arg-type]

    widget_sql, internal_sql = (str(c) for c in db.compiled)
    assert "widget_messages.org_id = " in widget_sql
    assert "widget_conversations.org_id = " in widget_sql
    assert "widget_conversations.is_preview IS false" in widget_sql
    assert "widget_conversations.is_test IS false" in widget_sql
    assert "internal_chat_turns.org_id = " in internal_sql
    for compiled in db.compiled:
        assert {v for k, v in compiled.params.items() if k.startswith("org_id")} == {7}


@pytest.mark.asyncio
async def test_below_full_telemetry_the_list_carries_no_visitor_text() -> None:
    signals = _signals(decision="partial_answer", problem="wachtmuziek instellen", open_parts=["na 18 uur"])
    db = _Db([_row(signals, visitor="hoe zet ik muziek in de wacht")], level="shadow")

    cluster = _only(await knowledge_gap_clusters(db, org_id=7, days=7, now=_T0))  # type: ignore[arg-type]

    assert (cluster["question"], cluster["questions"], cluster["open_parts"]) == (None, [], [])
    # The article title is the owner's text, so it shows below full as well.
    assert cluster["topic"] == "Wachtmuziek instellen"
    assert cluster["count"] == 1
    assert cluster["article"]["url"] == "https://help.example.com/wachtmuziek"


def _article(slug: str, title: str, host: str = "help.example.com") -> list[dict]:
    return [{"title": title, "url": f"https://{host}/{slug}", "score": 0.7}]


def test_two_wordings_on_the_same_article_share_one_cluster() -> None:
    """The article is what an owner fixes; the visitor's wording and the url's
    www prefix or trailing slash do not split the cluster."""
    turns = [
        _widget(
            minutes=1,
            decision="partial_answer",
            problem="wachtmuziek na sluitingstijd",
            found=_article("muziek", "Wachtmuziek"),
        ),
        _widget(
            minutes=2,
            decision="answer",
            gap_type="soft",
            problem="muziek tijdens het doorverbinden",
            found=_article("muziek/", "Wachtmuziek", host="www.help.example.com"),
        ),
        _widget(minutes=3, decision="clarifying_question", problem="geluid", found=_article("muziek", "Wachtmuziek")),
    ]

    cluster = _only(build_clusters(turns, {}))

    assert (cluster["count"], cluster["topic"], cluster["decidable"]) == (3, "Wachtmuziek", True)


def test_a_turn_without_an_article_to_anchor_on_is_grouped_on_its_words() -> None:
    """A refusal says no article answers it, so the article it happened to find is no anchor."""
    turns = [
        _widget(decision="refusal", problem="faxnummer koppelen", found=_article("muziek", "Wachtmuziek")),
        _widget(decision="refusal", problem="voicemail transcriptie", found=_article("muziek", "Wachtmuziek")),
        _widget(decision="partial_answer", problem="voicemail transcriptie", found=[{"title": "Zonder url"}]),
    ]

    clusters = build_clusters(turns, {})

    assert sorted((c["topic"], c["count"]) for c in clusters) == [
        ("faxnummer koppelen", 1),
        ("transcriptie voicemail", 2),
    ]


def test_a_problem_of_only_filler_falls_back_to_the_search_query() -> None:
    cluster = _only(
        build_clusters(
            [_widget(decision="refusal", problem="graag weten hoe dat lukt", found=[])],
            {},
        )
    )

    assert cluster["topic"] == "wachtmuziek"
    assert cluster["decidable"] is True


def test_chat_filler_never_becomes_a_topic_word() -> None:
    words = topic_words(
        "de bezoeker en klant willen graag weten hoe het lukt, mijn vraag aanvragen via nieuwe "
        "andere wijzigen toevoegen opnieuw wachtmuziek"
    )

    assert words == ["wachtmuziek"]


def test_a_plural_and_its_singular_are_one_topic_word() -> None:
    turns = [
        _widget(minutes=1, decision="refusal", problem="oproepen doorschakelen", found=[]),
        _widget(minutes=2, decision="refusal", problem="oproep doorschakelen", found=[]),
    ]

    cluster = _only(build_clusters(turns, {}))

    assert (cluster["count"], cluster["topic"]) == (2, "doorschakelen oproep")


def test_internal_aggregates_come_last_and_a_decidable_singleton_stays_listed() -> None:
    turns = [
        *(GapTurn("internal", {"decision": "refusal", "gap_type": "hard"}, _T0) for _ in range(5)),
        *(_widget(minutes=m, decision="human_request", problem="graag", query="kan dat?", found=[]) for m in range(3)),
        _widget(decision="refusal", problem="faxnummer", found=[]),
    ]

    clusters = build_clusters(turns, {})

    assert [(c["topic"], c["count"], c["decidable"]) for c in clusters] == [
        ("faxnummer", 1, True),
        ("wolkcentrale", 3, False),
        ("\u2014", 5, False),
    ]
    assert clusters[-1]["cluster_key"] == "internal|no_article"


def test_the_order_and_keys_are_the_same_for_shuffled_input() -> None:
    rng = random.Random(SEED)  # noqa: S311 -- a seeded, reproducible test generator, not a secret
    slugs = ["muziek", "voicemail", "doorschakelen"]
    turns = [
        _widget(
            minutes=i,
            decision=rng.choice(["partial_answer", "refusal", "human_request"]),
            problem=rng.choice(["wachtmuziek", "faxnummer koppelen", "oproepen doorschakelen", ""]),
            found=_article(rng.choice(slugs), "Artikel"),
        )
        for i in range(30)
    ]
    first = [(c["cluster_key"], c["count"], c["topic"]) for c in build_clusters(turns, {})]

    rng.shuffle(turns)
    second = [(c["cluster_key"], c["count"], c["topic"]) for c in build_clusters(turns, {})]

    assert first == second
