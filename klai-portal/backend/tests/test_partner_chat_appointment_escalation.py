"""The in-chat appointment offer: one signal, one message, never a marker.

The help-page bot may offer the visitor an appointment with a human. Until
now the widget could only express that as a permanent booking bar, always on
screen, unrelated to what the bot had just said. The escalation signal makes
the offer belong to the answer that made it:

* streaming — ``{"choices":[{"delta":{"escalation":{"appointment":true}}}]}``
* non-streaming — ``message["escalation"] = {"appointment": true}``
* absent = no offer; the shape is exactly ``{"appointment": bool}``, never
  half.

Two sources feed it. The backend knows about its own canned refusal (the
helpdesk text literally offers an appointment) and about the broad-mode
"offer" turn. It cannot know about an offer the model composed in its own
words, so the SUPPORT profile ends such a reply with a machine marker; the
composer strips that marker and converts it. The stripping is the part with
teeth: a marker that survives is a visible defect on a customer's help page,
so every seam below asserts the visitor text never contains it.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from helpers import FakeResult, make_partner_auth
from klai_chat_prompts import appointment_offer_marker, no_citable_sources_message

from app.api import partner
from app.services import partner_chat, widget_turn, widget_write
from app.services.case_file import Case
from app.services.chat_profile import ChatProfile
from app.services.escalation_intent import escalation_intent
from app.services.partner_chat import KnowledgeTurn, _compose_backend_managed_answer
from app.services.widget_reply import Reply, reply_body, reply_frames
from app.services.widget_write import write_reply

MARKER = appointment_offer_marker()
HUMAN_QUERY = "Kan er iemand van jullie hier eens naar kijken?"
NEGATIVE_QUERY = "Dit werkt al drie dagen niet en ik heb er genoeg van"
NEUTRAL_QUERY = "Hoe voeg ik een extra gebruiker toe?"
NEUTRAL = {"wants_human": False, "sentiment": "neutral"}


def _case(*, wants_human: bool = False, sentiment: str = "neutral") -> Case:
    """The case as the route reads it: a request for a person is a want, the tone a field."""
    return Case.model_validate(
        {
            "problem": "",
            "product": "",
            "variant": "",
            "tried": [],
            "wants": "human" if wants_human else "knowledge",
            "out_of_scope": False,
            "sentiment": sentiment,
            "queries": [],
        }
    )


def _good_chunk() -> dict[str, Any]:
    return {
        "chunk_id": "c1",
        "text": "Je reset het wachtwoord via Instellingen > Beveiliging.",
        "source_url": "https://example.com/reset",
        "reranker_score": 0.9,
    }


def _grounded_sources() -> list[dict[str, Any]]:
    return [{"label": "1", "title": "Reset", "url": "https://example.com/reset", "evidence_ids": ["c1"]}]


def _parse_frames(chunks: list[bytes]) -> list[dict]:
    out: list[dict] = []
    for raw in chunks:
        text = raw.decode()
        assert text.startswith("data: ")
        payload = text[6:].strip()
        if payload and payload != "[DONE]":
            out.append(json.loads(payload))
    return out


def _delta_values(frames: list[dict], key: str) -> list[Any]:
    values: list[Any] = []
    for frame in frames:
        for choice in frame.get("choices") or []:
            delta = choice.get("delta") or {}
            if key in delta:
                values.append(delta[key])
    return values


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "classification", "stream", "expected"),
    [
        (HUMAN_QUERY, {"wants_human": True}, False, True),
        (HUMAN_QUERY, {"wants_human": True}, True, True),
        (NEGATIVE_QUERY, {"wants_human": False, "sentiment": "negative"}, False, True),
        (NEUTRAL_QUERY, NEUTRAL, False, False),
        ("IK WIL EEN MEDEWERKER SPREKEN", NEUTRAL, False, True),
    ],
)
async def test_widget_classifier_controls_escalation(monkeypatch, query, classification, stream, expected):
    assert escalation_intent(HUMAN_QUERY) is escalation_intent(NEGATIVE_QUERY) is None

    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_classifier"
    request = partner.ChatCompletionsRequest(messages=[{"role": "user", "content": query}], stream=stream)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult())

    async def written(**kwargs):
        return Reply(
            text="Artikelantwoord",
            sources=[],
            signals={},
            language=None,
            turn=KnowledgeTurn(),
            appointment=kwargs["force_escalation"],
        )

    monkeypatch.setattr(partner, "_resolve_kb_slugs", AsyncMock(return_value=["kb-alpha"]))
    judge = AsyncMock(return_value=_case(**classification))
    monkeypatch.setattr(partner.case_file, "update_case", judge)
    monkeypatch.setattr(widget_turn, "retrieve_context", AsyncMock(return_value=([_good_chunk()], "prompt", [], False)))
    monkeypatch.setattr(widget_turn, "write_retrieval_log", AsyncMock())
    monkeypatch.setattr(widget_turn, "write_reply", written)

    response = await partner.chat_completions(
        request=request,
        http_request=MagicMock(headers={}, client=None),
        auth=auth,
        db=db,
    )

    if stream:
        frames = _parse_frames([chunk async for chunk in response.body_iterator])
        escalated = bool(_delta_values(frames, "escalation"))
    else:
        escalated = "escalation" in response["choices"][0]["message"]
    assert escalated is expected
    judge.assert_awaited_once()


# ─── composer: the backend's own two cases ───────────────────────────────


def test_helpdesk_refusal_offers_an_appointment():
    """The canned refusal ends with "plan dan een afspraak" — the offer is in
    the text, so the signal must be there too or the button never appears on
    the single most frequent answer the bot gives."""
    text, _sources, decision = _compose_backend_managed_answer(
        "Ik weet het ook niet.",
        [],
        [],
        "wat kost het abonnement?",
        helpdesk=True,
        response_language="nl",
    )
    assert text == no_citable_sources_message("nl", helpdesk=True)
    assert decision["escalation"] == {"appointment": True}
    # It is the same turn the broad-mode offer rides on; both signals stand.
    assert decision["broad_mode"] == "offer"


def test_refusal_over_found_articles_does_not_offer_a_broader_search():
    """A broad answer needs a turn where retrieval found nothing at all. Offered
    over articles that were found, the visitor agrees, retrieval finds articles
    again and the reply is a second "not found" (seen in the owner's review)."""
    _text, _sources, decision = _compose_backend_managed_answer(
        "Ik weet het ook niet.", [], [_good_chunk()], "wat kost het abonnement?", helpdesk=True, response_language="nl"
    )
    assert decision["escalation"] == {"appointment": True}
    assert "broad_mode" not in decision


def test_broad_mode_no_output_still_offers_an_appointment():
    # Consent was already given, so no broad re-offer — but the visitor is
    # looking at the refusal text, which does offer an appointment.
    _text, _sources, decision = _compose_backend_managed_answer(
        "   ", [], [], "wat kost het abonnement?", helpdesk=True, broad=True, response_language="nl"
    )
    assert decision["escalation"] == {"appointment": True}


def test_partner_refusal_carries_no_escalation():
    """Partner API callers never run the SUPPORT prompt and have no booking
    panel; their refusal wording does not offer anything."""
    _text, _sources, decision = _compose_backend_managed_answer(
        "Whatever the model said.", [], [], "what is the price", helpdesk=False, response_language="en"
    )
    assert "escalation" not in decision


# ─── composer: the offer the model wrote itself ──────────────────────────


def test_model_marker_becomes_the_signal_and_leaves_the_text():
    """A grounded answer that ALSO offers an appointment: the answer survives
    the citation firewall untouched, the marker does not survive at all."""
    answer = (
        "Je reset het wachtwoord via Instellingen > Beveiliging. "
        f"Lukt dat niet, dan plan ik een afspraak voor je in.\n\n{MARKER}"
    )
    text, sources, decision = _compose_backend_managed_answer(
        answer,
        _grounded_sources(),
        [_good_chunk()],
        "hoe reset ik mijn wachtwoord",
        helpdesk=True,
        response_language="nl",
    )
    assert decision["escalation"] == {"appointment": True}
    assert MARKER not in text
    assert "APPOINTMENT_OFFER" not in text.upper()
    assert text.startswith("Je reset het wachtwoord via Instellingen > Beveiliging.")
    # The offer says nothing about grounding: sources are unaffected.
    assert sources


def test_grounded_answer_without_the_marker_has_no_escalation():
    answer = "Je reset het wachtwoord via Instellingen > Beveiliging."
    _text, sources, decision = _compose_backend_managed_answer(
        answer,
        _grounded_sources(),
        [_good_chunk()],
        "hoe reset ik mijn wachtwoord",
        helpdesk=True,
        response_language="nl",
    )
    assert "escalation" not in decision
    assert sources


def test_broad_answer_marker_is_stripped_and_signals():
    answer = (
        "Nummerportering duurt in Nederland meestal een werkdag. "
        f"Wil je het zeker weten, plan dan een afspraak met een medewerker.\n{MARKER}"
    )
    text, _sources, decision = _compose_backend_managed_answer(
        answer,
        [],
        [],
        "hoe lang duurt portering?",
        helpdesk=True,
        broad=True,
        response_language="nl",
    )
    assert decision["escalation"] == {"appointment": True}
    assert MARKER not in text
    assert text.endswith("plan dan een afspraak met een medewerker.")


def test_partner_path_strips_the_marker_but_never_signals():
    """A marker on the partner path is model noise, not a contract. It is still
    removed — a visitor may never see it — but it grants no escalation."""
    answer = f"Here is the answer. {MARKER}"
    text, _sources, decision = _compose_backend_managed_answer(
        answer,
        _grounded_sources(),
        [_good_chunk()],
        "how do I reset",
        helpdesk=False,
        response_language="en",
    )
    assert MARKER not in text
    assert "escalation" not in decision


def test_marker_only_reply_is_not_rendered_as_a_bare_marker():
    """Degenerate output (nothing but the marker) must not turn into a message
    whose entire content is the token."""
    text, _sources, decision = _compose_backend_managed_answer(
        MARKER, [], [], "help me", helpdesk=True, response_language=None
    )
    assert MARKER not in text
    assert text == no_citable_sources_message("nl", helpdesk=True)
    assert decision["escalation"] == {"appointment": True}


# ─── the widget's reply: delta.escalation and message.escalation ─────────


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm:4000"
    settings.litellm_master_key = "key"
    return settings


def _model_says(monkeypatch, model_text: str) -> None:
    class _MockResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": model_text}, "finish_reason": "stop"}
                ],
            }

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            return _MockResp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _MockClient())


async def _written(monkeypatch, model_text: str, **kwargs) -> Reply:
    _model_says(monkeypatch, model_text)
    kwargs.setdefault("messages", [{"role": "user", "content": "ik kom er niet uit"}])
    kwargs.setdefault("source_query", "ik kom er niet uit")
    return await write_reply(
        model="klai-primary", temperature=0.7, system_prompt="sys", settings=_settings(), org_id=42, **kwargs
    )


async def _widget_frames(monkeypatch, model_text: str, **kwargs) -> list[dict]:
    return _parse_frames(reply_frames(await _written(monkeypatch, model_text, **kwargs)))


async def _widget_message(monkeypatch, model_text: str, **kwargs) -> dict:
    reply = await _written(monkeypatch, model_text, **kwargs)
    return reply_body(reply, model="klai-primary")["choices"][0]["message"]


@pytest.mark.asyncio
async def test_stream_emits_escalation_frame_for_a_model_offer(monkeypatch):
    # Grounded on purpose: the frame must come from the model's marker, not
    # from the canned refusal that also escalates.
    decisions = []
    monkeypatch.setattr(widget_write, "_log_citation_rescues", lambda decision, **_: decisions.append(decision))

    parsed = await _widget_frames(
        monkeypatch,
        "Je reset het wachtwoord via Instellingen > Beveiliging. "
        f"Lukt dat niet, dan plan ik een afspraak voor je in.\n\n{MARKER}",
        messages=[{"role": "user", "content": "hoe reset ik mijn wachtwoord"}],
        source_query="hoe reset ik mijn wachtwoord",
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
        sentiment="positive",
    )

    assert _delta_values(parsed, "escalation") == [{"appointment": True}]
    content = "".join(_delta_values(parsed, "content"))
    assert MARKER not in content
    assert "APPOINTMENT_OFFER" not in content.upper()
    # Not the canned refusal: the signal really came from the marker.
    assert content.startswith("Je reset het wachtwoord")
    assert decisions[0]["sentiment"] == "positive"


@pytest.mark.asyncio
async def test_stream_emits_escalation_frame_on_the_canned_refusal(monkeypatch):
    parsed = await _widget_frames(
        monkeypatch,
        "Ik ken het antwoord niet.",
        messages=[{"role": "user", "content": "wat kost het"}],
        source_query="wat kost het abonnement?",
        trusted_sources=[],
        citation_chunks=[],
    )

    assert _delta_values(parsed, "escalation") == [{"appointment": True}]


@pytest.mark.asyncio
async def test_stream_grounded_answer_emits_no_escalation_frame(monkeypatch):
    parsed = await _widget_frames(
        monkeypatch,
        "Je reset het wachtwoord via Instellingen > Beveiliging.",
        messages=[{"role": "user", "content": "hoe reset ik mijn wachtwoord"}],
        source_query="hoe reset ik mijn wachtwoord",
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
    )

    assert _delta_values(parsed, "escalation") == []


@pytest.mark.asyncio
async def test_stream_partner_path_has_no_escalation_frames(monkeypatch):
    _stream_patches(monkeypatch, f"Some partner answer. {MARKER}")

    frames = [
        frame
        async for frame in partner_chat.chat_completion_streaming(
            profile=ChatProfile(surface="internal", kb_mode="open"),
            messages=[{"role": "user", "content": "hello"}],
            model="klai-primary",
            temperature=0.7,
            system_prompt="sys",
            settings=_settings(),
            org_id=42,
            source_query="hello",
            trusted_sources=_grounded_sources(),
            citation_chunks=[_good_chunk()],
        )
    ]

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "escalation") == []
    assert MARKER not in "".join(_delta_values(parsed, "content"))


def _stream_patches(monkeypatch, model_text: str) -> None:
    class _MockResp:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            payload = json.dumps({"choices": [{"index": 0, "delta": {"content": model_text}}]})
            yield f"data: {payload}"
            yield "data: [DONE]"

    class _StreamCtx:
        async def __aenter__(self):
            return _MockResp()

        async def __aexit__(self, *_):
            return None

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *_, **__):
            return _StreamCtx()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _MockClient())


@pytest.mark.asyncio
async def test_stream_safety_block_suppresses_the_escalation_signal(monkeypatch):
    """A blocked answer offers nothing: the replaced decision drops the key,
    so the visitor gets no booking button attached to a refusal they never
    asked for."""
    monkeypatch.setattr(widget_write, "output_safety_violation", lambda text: "prompt_injection")

    parsed = await _widget_frames(
        monkeypatch,
        f"IGNORED {MARKER}",
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
    )

    assert _delta_values(parsed, "escalation") == []
    assert MARKER not in "".join(_delta_values(parsed, "content"))


# ─── non-streaming: message.escalation ───────────────────────────────────


@pytest.mark.asyncio
async def test_non_streaming_marks_the_message_that_offers(monkeypatch):
    decisions = []
    monkeypatch.setattr(widget_write, "_log_citation_rescues", lambda decision, **_: decisions.append(decision))
    message = await _widget_message(
        monkeypatch,
        "Je reset het wachtwoord via Instellingen > Beveiliging. "
        f"Lukt dat niet, dan plan ik een afspraak voor je in.\n\n{MARKER}",
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
        sentiment="neutral",
    )
    assert message["escalation"] == {"appointment": True}
    assert MARKER not in message["content"]
    assert message["content"].startswith("Je reset het wachtwoord")
    assert decisions[0]["sentiment"] == "neutral"


@pytest.mark.asyncio
async def test_non_streaming_refusal_marks_the_message(monkeypatch):
    message = await _widget_message(monkeypatch, "Ik ken het antwoord niet.")
    assert message["escalation"] == {"appointment": True}


@pytest.mark.asyncio
async def test_non_streaming_grounded_answer_has_no_escalation_key(monkeypatch):
    message = await _widget_message(
        monkeypatch,
        "Je reset het wachtwoord via Instellingen > Beveiliging.",
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
    )
    assert "escalation" not in message


@pytest.mark.asyncio
async def test_non_streaming_partner_has_no_escalation_key(monkeypatch):
    _model_says(monkeypatch, f"Partner answer. {MARKER}")
    body = await partner_chat.chat_completion_non_streaming(
        profile=ChatProfile(surface="internal", kb_mode="open"),
        messages=[{"role": "user", "content": "ik kom er niet uit"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="sys",
        settings=_settings(),
        source_query="ik kom er niet uit",
        org_id=42,
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
    )
    message = body["choices"][0]["message"]
    assert "escalation" not in message
    assert MARKER not in message["content"]


# --- backend-decided escalation: a matching article must not cancel the offer ---


def test_forced_escalation_sets_signal_on_grounded_answer() -> None:
    """The visitor asked for a person; retrieval found an article; the model wrote
    steps and no marker. Before the backend layer this produced no button at all
    (measured 2026-09-09 against the Voys widget). Now the decision carries the
    signal because the backend, not the model, made the call."""
    text = "Ga naar Instellingen > Beveiliging en reset daar je wachtwoord."
    content, sources, decision = _compose_backend_managed_answer(
        text,
        _grounded_sources(),
        [_good_chunk()],
        "IK WIL EEN MEDEWERKER SPREKEN",
        helpdesk=True,
        force_escalation=True,
        response_language="nl",
    )
    assert MARKER not in content
    assert sources, "the grounded answer keeps its sources"
    assert decision.get("escalation") == {"appointment": True}


def test_forced_escalation_is_ignored_off_the_helpdesk_path() -> None:
    """Partner-API callers never get the SUPPORT profile or the button."""
    _, _, decision = _compose_backend_managed_answer(
        "Some answer.",
        _grounded_sources(),
        [_good_chunk()],
        "I want a human",
        helpdesk=False,
        force_escalation=True,
        response_language="en",
    )
    assert "escalation" not in decision


def test_grounded_answer_without_force_or_marker_has_no_signal() -> None:
    _, _, decision = _compose_backend_managed_answer(
        "Ga naar Instellingen > Beveiliging.",
        _grounded_sources(),
        [_good_chunk()],
        "Hoe reset ik mijn wachtwoord?",
        helpdesk=True,
        response_language="nl",
    )
    assert "escalation" not in decision


def test_bare_marker_without_an_offer_in_the_text_is_ignored() -> None:
    """Measured 2026-09-09: one in six plain answers carried the marker but no
    offer sentence. The button must not appear under a reply that never
    mentions an appointment."""
    text = f"Ga naar Instellingen > Beveiliging en reset daar je wachtwoord.\n{MARKER}"
    content, _, decision = _compose_backend_managed_answer(
        text,
        _grounded_sources(),
        [_good_chunk()],
        "Hoe voeg ik een gebruiker toe?",
        helpdesk=True,
        response_language="nl",
    )
    assert MARKER not in content
    assert "escalation" not in decision


def test_marker_with_a_real_offer_in_the_text_still_counts() -> None:
    text = f"Je kunt een afspraak inplannen met een medewerker via de knop hieronder.\n{MARKER}"
    _, _, decision = _compose_backend_managed_answer(
        text,
        _grounded_sources(),
        [_good_chunk()],
        "Ik wil iemand spreken",
        helpdesk=True,
        response_language="nl",
    )
    assert decision.get("escalation") == {"appointment": True}


def test_forced_escalation_needs_no_offer_sentence() -> None:
    """The backend decided; the button follows even if the model wrote only steps."""
    _, _, decision = _compose_backend_managed_answer(
        "**Stap 1:** Ga naar Beheer.",
        _grounded_sources(),
        [_good_chunk()],
        "IK WIL EEN MEDEWERKER SPREKEN",
        helpdesk=True,
        force_escalation=True,
        response_language="nl",
    )
    assert decision.get("escalation") == {"appointment": True}
