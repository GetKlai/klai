"""escalation_intent: the backend's own read of 'wants a person' / 'is angry'."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from app.services import escalation_intent as escalation_module
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


def test_wanting_to_set_something_up_with_an_employee_is_a_human_request() -> None:
    """Reported 2026-09-09: 'Heej ik wil dit graag met een medewerker instellen.'
    got an article answer and no button — the request for a person was phrased
    as doing something WITH one, not speaking TO one."""
    assert escalation_intent("Heej ik wil dit graag met een medewerker instellen.") == "human_request"
    assert escalation_intent("Ik wil een afspraak maken met een medewerker") == "human_request"
    assert escalation_intent("Kan ik dit samen met iemand van jullie doen?") == "human_request"


@pytest.mark.asyncio
async def test_classifier_is_structured_and_failure_safe(monkeypatch) -> None:
    valid = {"wants_human": True, "sentiment": "neutral"}
    provider_results = iter([valid, "invalid", RuntimeError("offline"), "slow"])
    original_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        provider_result = next(provider_results)
        assert b'"model":"klai-fast"' in request.content and b'"type":"json_schema"' in request.content
        if isinstance(provider_result, Exception):
            raise provider_result
        if provider_result == "slow":
            await asyncio.sleep(3)
        content = provider_result if isinstance(provider_result, str) else '{"wants_human":true,"sentiment":"neutral"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(
        escalation_module.httpx,
        "AsyncClient",
        lambda timeout: original_client(transport=httpx.MockTransport(handler)),
    )
    settings = SimpleNamespace(
        litellm_base_url="http://litellm", litellm_master_key="key", extraction_model="klai-fast"
    )

    assert await escalation_module.classify_escalation("visitor text", settings) == valid
    for _ in range(3):
        assert await escalation_module.classify_escalation("visitor text", settings) is None
