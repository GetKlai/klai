"""escalation_intent: the backend's own read of 'wants a person' / 'is angry'."""

from __future__ import annotations

import pytest

from app.services.escalation_intent import FRUSTRATION, HUMAN_REQUEST, escalation_intent


@pytest.mark.parametrize(
    "text",
    [
        "IK WIL EEN MEDEWERKER SPREKEN",
        "Ik wil graag hulp van een medewerker om dit te doen?",
        "Kan ik iemand spreken?",
        "ik wil gewoon een mens, geen chatbot",
        "Mag ik met een collega spreken?",
        "I want to talk to a human",
        "Can I speak with a real person?",
        "not a bot please",
    ],
)
def test_asking_for_a_person_is_a_human_request(text: str) -> None:
    assert escalation_intent(text) == HUMAN_REQUEST


@pytest.mark.parametrize(
    "text",
    [
        "Ik ben al drie keer doorverbonden en niemand lost het op. Ik ben het echt zat.",
        "Dit is belachelijk, het werkt nog steeds niet.",
        "This is the third time I'm asking and nobody helps.",
        "WAAROM DOET MIJN NUMMER HET NOG STEEDS NIET",
    ],
)
def test_anger_and_repetition_are_frustration(text: str) -> None:
    assert escalation_intent(text) == FRUSTRATION


@pytest.mark.parametrize(
    "text",
    [
        "Hoe voeg ik een extra gebruiker toe?",
        "De medewerker vertelde dat ik het belplan moet aanpassen.",  # a report, not a request
        "Waar vind ik het medewerkersportaal?",  # substring, not the word
        "OK",  # too short to be shouting
        "Hoe werkt een belgroep?",
        "",
        None,
        42,
    ],
)
def test_ordinary_questions_do_not_escalate(text: object) -> None:
    assert escalation_intent(text) is None
