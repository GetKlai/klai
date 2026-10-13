"""The selection step's checks in code: a quoted sentence counts from the passage that carries it."""

from __future__ import annotations

from app.services.passage_selection import Evidence, PassageSelection

_STEP = "Sluit het basisstation van de headset aan op je toestel met de meegeleverde USB-kabel."
_SECTIONS = [
    {"chunk_id": "a", "text": "Over de headset. De headset werkt draadloos binnen tien meter van het basisstation."},
    {"chunk_id": "b", "text": "Aansluiten. " + _STEP + " Gebruik de aansluiting aan de onderkant."},
    {"chunk_id": "c", "text": "Opladen. Zet de headset op het basisstation om hem op te laden."},
]


def test_a_quote_under_the_wrong_passage_number_counts_for_the_passage_that_carries_it():
    """Retrieval hands over near-identical sections of one article; the model
    copied the right sentence and numbered the wrong section in two meetlat
    turns, which turned an answer into "not found"."""
    selection = PassageSelection(need="", evidence=[Evidence(passage=1, quote=_STEP)], verdict="answers", question="")

    assert [chunk["chunk_id"] for chunk in selection.chosen(_SECTIONS)] == ["b"]
    assert selection.not_in_passages(_SECTIONS) is False


def test_the_pointed_passage_wins_when_it_carries_the_quote_too():
    twice = [_SECTIONS[1], {"chunk_id": "d", "text": _STEP}]
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
