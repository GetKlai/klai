"""The selection step's checks in code: a quoted sentence counts from the passage that carries it."""

from __future__ import annotations

from app.services.passage_selection import Evidence, PassageSelection

_STEP = "Sluit het basisstation van de headset aan op je toestel met de meegeleverde USB-kabel."
_SECTIONS = [
    {"chunk_id": "a", "title": "Headset", "text": "Over de headset. De headset werkt draadloos binnen tien meter."},
    {"chunk_id": "b", "title": "Headset", "text": "Aansluiten. " + _STEP + " Gebruik de aansluiting aan de onderkant."},
    {"chunk_id": "c", "title": "Headset", "text": "Opladen. Zet de headset op het basisstation om hem op te laden."},
]


def test_a_quote_under_the_wrong_passage_number_counts_for_the_passage_that_carries_it():
    """Retrieval hands over near-identical sections of one article; the model
    copied the right sentence and numbered the wrong section in two meetlat
    turns, which turned an answer into "not found"."""
    selection = PassageSelection(need="", evidence=[Evidence(passage=1, quote=_STEP)], verdict="answers", question="")

    assert [chunk["chunk_id"] for chunk in selection.chosen(_SECTIONS)] == ["b"]
    assert selection.not_in_passages(_SECTIONS) is False


def test_a_quote_never_moves_to_another_article():
    """A like-worded sentence in another article can be about another product."""
    other = [
        {"chunk_id": "x", "title": "Facturen", "text": "Over facturen."},
        {"chunk_id": "y", "title": "App", "text": _STEP},
    ]
    selection = PassageSelection(need="", evidence=[Evidence(passage=1, quote=_STEP)], verdict="answers", question="")

    assert selection.chosen(other) == []
    assert selection.not_in_passages(other) is True


def test_a_shared_title_is_not_the_same_article():
    """Two articles named alike: the fallback follows the source, not the name."""
    two = [
        {"chunk_id": "p", "title": "Installatie", "source_url": "https://help.example.com/app", "text": "Over de app."},
        {"chunk_id": "q", "title": "Installatie", "source_url": "https://help.example.com/toestel", "text": _STEP},
    ]
    selection = PassageSelection(need="", evidence=[Evidence(passage=1, quote=_STEP)], verdict="answers", question="")

    assert selection.chosen(two) == []


def test_words_scattered_over_three_sentences_do_not_outrank_the_verdict():
    scattered = {
        "chunk_id": "s",
        "title": "Headset",
        "text": "Sluit de deur. Het basisstation staat op de kast. De headset ligt op je bureau. Je Yealink-apparaat "
        "heeft een USB-kabel aan de meegeleverde voet.",
    }
    selection = PassageSelection(
        need="", evidence=[Evidence(passage=1, quote=_STEP)], verdict="not_in_passages", question=""
    )

    assert selection.not_in_passages([scattered]) is True
    assert selection.not_in_passages(_SECTIONS) is False, "the same sentence in one piece still counts"


def test_a_few_common_words_do_not_outrank_a_not_in_passages_verdict():
    short = [{"chunk_id": "f", "title": "Fax", "text": "Faxen. Dat kan niet meer sinds 2024."}]
    selection = PassageSelection(
        need="", evidence=[Evidence(passage=1, quote="Dat kan niet.")], verdict="not_in_passages", question=""
    )

    assert selection.not_in_passages(short) is True


def test_the_pointed_passage_wins_when_it_carries_the_quote_too():
    twice = [_SECTIONS[1], {"chunk_id": "d", "title": "Headset", "text": _STEP}]
    selection = PassageSelection(need="", evidence=[Evidence(passage=2, quote=_STEP)], verdict="answers", question="")

    assert [chunk["chunk_id"] for chunk in selection.chosen(twice)] == ["d"]


def test_a_verified_quote_outranks_a_not_in_passages_verdict():
    """Five replay rounds: 39 "not in the passages" verdicts came with a sentence
    that stood in a passage, and the owner expected an answer in 31 of them."""
    selection = PassageSelection(
        need="", evidence=[Evidence(passage=2, quote=_STEP)], verdict="not_in_passages", question=""
    )

    assert selection.not_in_passages(_SECTIONS) is False
    assert [chunk["chunk_id"] for chunk in selection.chosen(_SECTIONS)] == ["b"]


def test_without_any_quote_the_verdict_decides():
    selection = PassageSelection(need="", evidence=[], verdict="not_in_passages", question="")
    assert selection.not_in_passages(_SECTIONS) is True
    thanks = PassageSelection(need="", evidence=[], verdict="answers", question="")
    assert thanks.not_in_passages(_SECTIONS) is False


def test_a_quote_that_stands_in_no_passage_still_means_not_in_passages():
    selection = PassageSelection(
        need="",
        evidence=[Evidence(passage=2, quote="Druk drie seconden op de resetknop.")],
        verdict="answers",
        question="",
    )

    assert selection.chosen(_SECTIONS) == []
    assert selection.not_in_passages(_SECTIONS) is True
