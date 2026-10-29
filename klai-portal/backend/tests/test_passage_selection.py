"""The selection step's checks in code: a quoted sentence counts from the passage that carries it."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import passage_selection
from app.services.passage_selection import Evidence, Part, PassageSelection, writer_brief

_STEP = "Sluit het basisstation van de headset aan op je toestel met de meegeleverde USB-kabel."
_SECTIONS = [
    {"chunk_id": "a", "title": "Headset", "text": "Over de headset. De headset werkt draadloos binnen tien meter."},
    {"chunk_id": "b", "title": "Headset", "text": "Aansluiten. " + _STEP + " Gebruik de aansluiting aan de onderkant."},
    {"chunk_id": "c", "title": "Headset", "text": "Opladen. Zet de headset op het basisstation om hem op te laden."},
]


def _selection(
    *evidence: Evidence, need: str = "", ask: str = "vraag", must_ask_variant: bool = False, question: str = ""
) -> PassageSelection:
    """A selection with one part: the visitor's ask and the sentences found for it."""
    return PassageSelection(
        need=need, parts=[Part(ask=ask, evidence=list(evidence))], must_ask_variant=must_ask_variant, question=question
    )


def test_a_quote_under_the_wrong_passage_number_counts_for_the_passage_that_carries_it():
    """Retrieval hands over near-identical sections of one article; the model
    copied the right sentence and numbered the wrong section in two meetlat
    turns, which turned an answer into "not found"."""
    selection = _selection(Evidence(passage=1, quote=_STEP))

    assert [chunk["chunk_id"] for chunk in selection.chosen(_SECTIONS)] == ["b"]
    assert selection.not_in_passages(_SECTIONS) is False


def test_a_quote_never_moves_to_another_article():
    """A like-worded sentence in another article can be about another product."""
    other = [
        {"chunk_id": "x", "title": "Facturen", "text": "Over facturen."},
        {"chunk_id": "y", "title": "App", "text": _STEP},
    ]
    selection = _selection(Evidence(passage=1, quote=_STEP))

    assert selection.chosen(other) == []
    assert selection.not_in_passages(other) is True


def test_a_shared_title_is_not_the_same_article():
    """Two articles named alike: the fallback follows the source, not the name."""
    two = [
        {"chunk_id": "p", "title": "Installatie", "source_url": "https://help.example.com/app", "text": "Over de app."},
        {"chunk_id": "q", "title": "Installatie", "source_url": "https://help.example.com/toestel", "text": _STEP},
    ]
    selection = _selection(Evidence(passage=1, quote=_STEP))

    assert selection.chosen(two) == []


def test_the_pointed_passage_wins_when_it_carries_the_quote_too():
    twice = [_SECTIONS[1], {"chunk_id": "d", "title": "Headset", "text": _STEP}]
    selection = _selection(Evidence(passage=2, quote=_STEP))

    assert [chunk["chunk_id"] for chunk in selection.chosen(twice)] == ["d"]


def test_nothing_is_not_found_unless_the_visitor_asked_something_and_no_part_has_a_sentence():
    asked = _selection()
    assert asked.not_in_passages(_SECTIONS) is True
    assert asked.verdict(_SECTIONS) == "not_in_passages"

    # A thank-you, a request for a person or a remark about the chat asks nothing of the articles.
    thanks = PassageSelection(need="", parts=[], must_ask_variant=False, question="")
    assert thanks.not_in_passages(_SECTIONS) is False
    assert thanks.verdict(_SECTIONS) == "answers"

    # One answered part among unanswered ones is an answer, and the steps may still vary.
    varying = PassageSelection(
        need="",
        parts=[Part(ask="a", evidence=[]), Part(ask="b", evidence=[Evidence(passage=2, quote=_STEP)])],
        must_ask_variant=True,
        question="Welke headset?",
    )
    assert varying.not_in_passages(_SECTIONS) is False
    assert varying.verdict(_SECTIONS) == "depends"


def test_a_quote_that_stands_in_no_passage_still_means_not_in_passages():
    selection = _selection(Evidence(passage=2, quote="Druk drie seconden op de resetknop."))

    assert selection.chosen(_SECTIONS) == []
    assert selection.not_in_passages(_SECTIONS) is True
    # The record reads the same checked verdict the route does.
    assert selection.verdict(_SECTIONS) == "not_in_passages"


def test_the_brief_speaks_the_words_of_the_surface_it_is_written_for():
    """The internal chat's user is an employee reading the knowledge base; a
    brief about "help articles" and "the visitor" made Strict answer an
    employee with "the help articles do not cover this"."""
    selection = _selection(Evidence(passage=1, quote=_STEP), need="aansluiten")

    widget = writer_brief(selection, _SECTIONS, internal=False, source_only=True)
    internal = writer_brief(selection, _SECTIONS, internal=True, source_only=True)

    assert "What the visitor needs" in widget and "help articles" in widget
    assert "What the user needs" in internal and "knowledge base" in internal
    assert "visitor" not in internal and "help article" not in internal
    assert _STEP in widget and _STEP in internal


def test_open_mode_is_not_held_to_the_source_s_own_words():
    """Open may fill from general knowledge; the stricter sentence made its
    answers worse in two internal rounds, Strict's better."""
    selection = _selection(Evidence(passage=1, quote=_STEP), need="aansluiten")

    strict = writer_brief(selection, _SECTIONS, internal=True, source_only=True)
    open_mode = writer_brief(selection, _SECTIONS, internal=True, source_only=False)

    assert "own order and its own words" in strict
    assert "own order and its own words" not in open_mode
    assert "Do not add a step, menu, cause or example that is not in the source." in open_mode


def test_open_mode_translates_the_source_lines_too():
    """An English question on a Dutch knowledge base: on 29 October 2026 the
    internal Open writer, briefed with the Dutch source lines and told to
    build on them, answered in Dutch under a "Respond in English" instruction.
    Only the Strict brief said to translate."""
    selection = _selection(Evidence(passage=1, quote=_STEP), need="aansluiten")

    strict = writer_brief(selection, _SECTIONS, internal=True, source_only=True)
    open_mode = writer_brief(selection, _SECTIONS, internal=True, source_only=False)

    assert "translated into the user's language" in strict
    assert "translated into the user's language" in open_mode


def test_a_turn_answered_from_the_passages_is_not_also_told_to_say_the_articles_lack_something():
    """A turn either answers from the passages or says it cannot, never both.

    Shaped like a widget follow-up of 2026-10-23: the selection quoted the one
    sentence that lists every app and the reply still opened with "this is not
    in the help articles" before the list. The brief asks every writer to name
    what the articles do not cover, so the writer decided that gap itself on a
    turn the selection already found whole."""
    apps = "Met Belhoek kun je bellen via de Belhoek Webtelefoon, de Belhoek App en de softphone Kiepsoft Duo."
    article = [{"chunk_id": "s", "title": "Softphones en apps", "text": "Softphones en apps. " + apps}]
    selection = _selection(
        Evidence(passage=1, quote=apps), need="Welke softphones de bezoeker met Belhoek kan gebruiken."
    )

    brief = writer_brief(selection, article, internal=False, source_only=True)

    assert apps in brief
    assert "does not cover" not in brief


def test_a_part_the_visitor_did_not_ask_is_never_named_as_not_covered():
    """The free-text gap named the visitor's own situation in 4 of 18 widget
    meetlat cases ("whether the base station is switched on"): a fact about
    the visitor's setup is not a part of their question, so it is neither
    named nor a reason for the appointment button."""
    visitor = ["Hoe sluit ik de headset aan op mijn toestel?"]
    selection = PassageSelection(
        need="Hoe de headset aansluit.",
        parts=[
            Part(ask="Hoe sluit ik de headset aan op mijn toestel?", evidence=[Evidence(passage=2, quote=_STEP)]),
            Part(ask="Staat het basisstation aan", evidence=[]),
        ],
        must_ask_variant=False,
        question="",
    )

    open_parts = selection.open_parts(_SECTIONS, visitor)
    brief = writer_brief(selection, _SECTIONS, internal=False, source_only=True, open_parts=open_parts)

    assert open_parts == []
    assert "answer all of it" in brief
    assert "do not cover" not in brief and "basisstation aan" not in brief


def test_an_asked_part_no_sentence_answers_is_the_one_gap_the_writer_names_after_the_answer():
    """The ask may stand in an earlier turn: a short follow-up narrows an earlier question."""
    visitor = ["Hoe sluit ik de headset aan en hoe laad ik de headset op?", "Op mijn bureautoestel."]
    selection = PassageSelection(
        need="Aansluiten en opladen.",
        parts=[
            Part(ask="Hoe sluit ik de headset aan", evidence=[Evidence(passage=2, quote=_STEP)]),
            Part(ask="hoe laad ik de headset op", evidence=[Evidence(passage=9, quote="Laad hem draadloos op.")]),
        ],
        must_ask_variant=False,
        question="",
    )

    open_parts = selection.open_parts(_SECTIONS, visitor)
    brief = writer_brief(selection, _SECTIONS, internal=False, source_only=True, open_parts=open_parts)

    assert open_parts == ["hoe laad ik de headset op"]
    assert "do not cover this part: hoe laad ik de headset op. After the answer" in brief
    assert "answer all of it" not in brief


def test_a_depends_verdict_that_asks_about_one_part_never_claims_the_lines_answer_all_of_it():
    question = "Gaat het om de draadloze of de bedrade headset?"
    selection = _selection(
        Evidence(passage=2, quote=_STEP),
        need="Hoe de headset aansluit en hoe hij oplaadt.",
        must_ask_variant=True,
        question=question,
    )

    brief = writer_brief(selection, _SECTIONS, internal=False, source_only=True, question=question)

    assert f"ask: {question}" in brief
    assert "answer all of it" not in brief


def test_questions_beyond_the_search_limit_keep_their_own_note_beside_the_brief():
    """The questions past the search limit are told apart from "not covered"
    by their own note; the brief must not tell the writer the lines answer
    everything while those questions were never searched."""
    selection = _selection(Evidence(passage=2, quote=_STEP), need="aansluiten")

    brief = writer_brief(selection, _SECTIONS, internal=False, source_only=True, unchecked=True)

    assert "answer all of it" not in brief
    assert "not separately searched" in brief


@pytest.mark.parametrize(
    ("prompt_name", "internal"),
    [
        ("SUPPORT_CHAT_SYSTEM_PROMPT", False),
        ("SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT", False),
        ("GROUNDED_CHAT_SYSTEM_PROMPT", True),
        ("OPEN_KB_CHAT_SYSTEM_PROMPT", True),
    ],
)
def test_every_profile_the_brief_reaches_defers_what_is_missing_to_the_brief_it_gets(prompt_name, internal):
    """One owner of "the sources do not cover this": each profile names the
    note by the heading the brief really writes, or the writer keeps its own
    rule beside the brief's."""
    import klai_chat_prompts

    selection = _selection(Evidence(passage=2, quote=_STEP), need="aansluiten")
    heading = writer_brief(selection, _SECTIONS, internal=internal, source_only=True).split("\n")[2]
    note = heading.removeprefix("[This turn] ").split(":")[0]

    assert f"When a note '{note}' follows" in getattr(klai_chat_prompts, prompt_name)


async def test_a_selection_that_fails_once_is_tried_again_and_used():
    """Without a selection a source-only turn answers "not found", so one timeout
    or bad reply gets a second try before it costs the visitor the answer."""
    selection = _selection(Evidence(passage=2, quote=_STEP), need="aansluiten")
    call = AsyncMock(side_effect=[None, selection])
    with patch.object(passage_selection, "structured_judge_call", call):
        result = await passage_selection.select_passages(
            [{"role": "user", "content": "Hoe sluit ik de headset aan?"}], _SECTIONS, MagicMock()
        )

    assert result is selection
    assert call.await_count == 2


async def test_the_selection_reads_the_last_part_of_a_long_message():
    """The week's multi-part messages ran 1,100 to 2,600 characters; clipped at
    800 the selection never saw the later questions."""
    last_part = "En hoe sluit ik daarna de headset aan op het toestel?"
    message = "Ik heb een vraag over de headset. " * 57 + last_part
    call = AsyncMock(return_value=None)
    with patch.object(passage_selection, "structured_judge_call", call):
        await passage_selection.select_passages([{"role": "user", "content": message}], _SECTIONS, MagicMock())

    assert len(message) > 1900
    assert last_part in call.await_args.kwargs["user_content"]
