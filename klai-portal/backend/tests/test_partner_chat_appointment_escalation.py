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
from app.services import partner_chat
from app.services.escalation_intent import escalation_intent
from app.services.partner_chat import (
    _chat_completion_streaming_with_composed_citations,
    _compose_backend_managed_answer,
)

MARKER = appointment_offer_marker()
HUMAN_QUERY = "Kan er iemand van jullie hier eens naar kijken?"
NEGATIVE_QUERY = "Dit werkt al drie dagen niet en ik heb er genoeg van"
NEUTRAL_QUERY = "Hoe voeg ik een extra gebruiker toe?"
NEUTRAL = {"wants_human": False, "sentiment": "neutral"}


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
    ("query", "classification", "stream", "support_mode", "expected"),
    [
        (HUMAN_QUERY, {"wants_human": True}, False, True, True),
        (HUMAN_QUERY, {"wants_human": True}, True, True, True),
        (NEGATIVE_QUERY, {"wants_human": False, "sentiment": "negative"}, False, True, True),
        (NEUTRAL_QUERY, NEUTRAL, False, True, False),
        ("IK WIL EEN MEDEWERKER SPREKEN", NEUTRAL, False, True, True),
        (NEUTRAL_QUERY, {"wants_human": True}, False, False, False),
    ],
)
async def test_widget_classifier_controls_escalation(
    monkeypatch, query, classification, stream, support_mode, expected
):
    assert escalation_intent(HUMAN_QUERY) is escalation_intent(NEGATIVE_QUERY) is None

    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_classifier"
    request = partner.ChatCompletionsRequest(messages=[{"role": "user", "content": query}], stream=stream)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult())

    async def completion(**kwargs):
        message = {"role": "assistant", "content": "Artikelantwoord", "sources": []}
        if kwargs["force_escalation"]:
            message["escalation"] = {"appointment": True}
        return {"choices": [{"message": message}]}

    async def streaming(**kwargs):
        if kwargs["force_escalation"]:
            yield b'data: {"choices":[{"delta":{"escalation":{"appointment":true}}}]}\n\n'
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(partner, "_resolve_kb_slugs", AsyncMock(return_value=["kb-alpha"]))
    monkeypatch.setattr(partner, "_widget_support_mode_enabled", AsyncMock(return_value=support_mode))
    monkeypatch.setattr(partner, "retrieve_context", AsyncMock(return_value=([_good_chunk()], "prompt", [], False)))
    classifier = AsyncMock(return_value=classification)
    monkeypatch.setattr(partner.escalation_service, "classify_escalation", classifier)
    monkeypatch.setattr(partner, "chat_completion_non_streaming", completion)
    monkeypatch.setattr(partner, "chat_completion_streaming", streaming)
    monkeypatch.setattr(partner, "write_retrieval_log", AsyncMock())

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
    assert classifier.await_count == int(support_mode)


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
        visitor_query="wat kost het abonnement?",
    )
    assert text == no_citable_sources_message("nl", helpdesk=True)
    assert decision["escalation"] == {"appointment": True}
    # It is the same turn the broad-mode offer rides on; both signals stand.
    assert decision["broad_mode"] == "offer"


def test_broad_mode_no_output_still_offers_an_appointment():
    # Consent was already given, so no broad re-offer — but the visitor is
    # looking at the refusal text, which does offer an appointment.
    _text, _sources, decision = _compose_backend_managed_answer(
        "   ", [], [], "wat kost het abonnement?", helpdesk=True, broad=True, visitor_query="wat kost het abonnement?"
    )
    assert decision["escalation"] == {"appointment": True}


def test_partner_refusal_carries_no_escalation():
    """Partner API callers never run the SUPPORT prompt and have no booking
    panel; their refusal wording does not offer anything."""
    _text, _sources, decision = _compose_backend_managed_answer(
        "Whatever the model said.", [], [], "what is the price", helpdesk=False, visitor_query="what is the price"
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
        visitor_query="hoe reset ik mijn wachtwoord",
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
        visitor_query="hoe reset ik mijn wachtwoord",
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
        visitor_query="hoe lang duurt portering?",
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
        visitor_query="how do I reset",
    )
    assert MARKER not in text
    assert "escalation" not in decision


def test_marker_only_reply_is_not_rendered_as_a_bare_marker():
    """Degenerate output (nothing but the marker) must not turn into a message
    whose entire content is the token."""
    text, _sources, decision = _compose_backend_managed_answer(
        MARKER, [], [], "help me", helpdesk=True, visitor_query="help me"
    )
    assert MARKER not in text
    assert text == no_citable_sources_message("nl", helpdesk=True)
    assert decision["escalation"] == {"appointment": True}


# ─── streaming: delta.escalation ─────────────────────────────────────────


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


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm:4000"
    settings.litellm_master_key = "key"
    return settings


async def _collect(**kwargs) -> list[bytes]:
    return [frame async for frame in _chat_completion_streaming_with_composed_citations(**kwargs)]


@pytest.mark.asyncio
async def test_stream_emits_escalation_frame_for_a_model_offer(monkeypatch):
    # Grounded on purpose: the frame must come from the model's marker, not
    # from the canned refusal that also escalates.
    _stream_patches(
        monkeypatch,
        "Je reset het wachtwoord via Instellingen > Beveiliging. "
        f"Lukt dat niet, dan plan ik een afspraak voor je in.\n\n{MARKER}",
    )
    decisions = []
    monkeypatch.setattr(partner_chat, "_log_citation_rescues", lambda decision, **_: decisions.append(decision))

    frames = await _collect(
        augmented_messages=[{"role": "user", "content": "hoe reset ik mijn wachtwoord"}],
        model="klai-primary",
        temperature=0.7,
        settings=_settings(),
        org_id=42,
        user_query="hoe reset ik mijn wachtwoord",
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
        support_mode=True,
        sentiment="positive",
    )

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "escalation") == [{"appointment": True}]
    content = "".join(_delta_values(parsed, "content"))
    assert MARKER not in content
    assert "APPOINTMENT_OFFER" not in content.upper()
    # Not the canned refusal: the signal really came from the marker.
    assert content.startswith("Je reset het wachtwoord")
    assert decisions[0]["sentiment"] == "positive"


@pytest.mark.asyncio
async def test_stream_emits_escalation_frame_on_the_canned_refusal(monkeypatch):
    _stream_patches(monkeypatch, "Ik ken het antwoord niet.")

    frames = await _collect(
        augmented_messages=[{"role": "user", "content": "wat kost het"}],
        model="klai-primary",
        temperature=0.7,
        settings=_settings(),
        org_id=42,
        user_query="wat kost het abonnement?",
        trusted_sources=[],
        citation_chunks=[],
        support_mode=True,
    )

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "escalation") == [{"appointment": True}]


@pytest.mark.asyncio
async def test_stream_grounded_answer_emits_no_escalation_frame(monkeypatch):
    _stream_patches(monkeypatch, "Je reset het wachtwoord via Instellingen > Beveiliging.")

    frames = await _collect(
        augmented_messages=[{"role": "user", "content": "hoe reset ik mijn wachtwoord"}],
        model="klai-primary",
        temperature=0.7,
        settings=_settings(),
        org_id=42,
        user_query="hoe reset ik mijn wachtwoord",
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
        support_mode=True,
    )

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "escalation") == []


@pytest.mark.asyncio
async def test_stream_partner_path_has_no_escalation_frames(monkeypatch):
    _stream_patches(monkeypatch, f"Some partner answer. {MARKER}")

    frames = await _collect(
        augmented_messages=[{"role": "user", "content": "hello"}],
        model="klai-primary",
        temperature=0.7,
        settings=_settings(),
        org_id=42,
        user_query="hello",
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
        support_mode=False,
    )

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "escalation") == []
    assert MARKER not in "".join(_delta_values(parsed, "content"))


@pytest.mark.asyncio
async def test_stream_safety_block_suppresses_the_escalation_signal(monkeypatch):
    """A blocked answer offers nothing: the replaced decision drops the key,
    so the visitor gets no booking button attached to a refusal they never
    asked for."""
    _stream_patches(monkeypatch, f"IGNORED {MARKER}")
    monkeypatch.setattr(partner_chat, "output_safety_violation", lambda text: "prompt_injection")

    frames = await _collect(
        augmented_messages=[{"role": "user", "content": "ik kom er niet uit"}],
        model="klai-primary",
        temperature=0.7,
        settings=_settings(),
        org_id=42,
        user_query="ik kom er niet uit",
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
        support_mode=True,
    )

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "escalation") == []
    assert MARKER not in "".join(_delta_values(parsed, "content"))


# ─── non-streaming: message.escalation ───────────────────────────────────


async def _call_non_streaming(monkeypatch, model_text: str, **kwargs):
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
    kwargs.setdefault("org_id", 42)
    return await partner_chat.chat_completion_non_streaming(
        messages=[{"role": "user", "content": "ik kom er niet uit"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="sys",
        settings=_settings(),
        citation_output="markers",
        source_query="ik kom er niet uit",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_non_streaming_marks_the_message_that_offers(monkeypatch):
    decisions = []
    monkeypatch.setattr(partner_chat, "_log_citation_rescues", lambda decision, **_: decisions.append(decision))
    body = await _call_non_streaming(
        monkeypatch,
        "Je reset het wachtwoord via Instellingen > Beveiliging. "
        f"Lukt dat niet, dan plan ik een afspraak voor je in.\n\n{MARKER}",
        support_mode=True,
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
        sentiment="neutral",
    )
    message = body["choices"][0]["message"]
    assert message["escalation"] == {"appointment": True}
    assert MARKER not in message["content"]
    assert message["content"].startswith("Je reset het wachtwoord")
    assert decisions[0]["sentiment"] == "neutral"


@pytest.mark.asyncio
async def test_non_streaming_refusal_marks_the_message(monkeypatch):
    body = await _call_non_streaming(monkeypatch, "Ik ken het antwoord niet.", support_mode=True)
    message = body["choices"][0]["message"]
    assert message["escalation"] == {"appointment": True}


@pytest.mark.asyncio
async def test_non_streaming_grounded_answer_has_no_escalation_key(monkeypatch):
    body = await _call_non_streaming(
        monkeypatch,
        "Je reset het wachtwoord via Instellingen > Beveiliging.",
        support_mode=True,
        trusted_sources=_grounded_sources(),
        citation_chunks=[_good_chunk()],
    )
    message = body["choices"][0]["message"]
    assert "escalation" not in message


@pytest.mark.asyncio
async def test_non_streaming_partner_has_no_escalation_key(monkeypatch):
    body = await _call_non_streaming(
        monkeypatch,
        f"Partner answer. {MARKER}",
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
        visitor_query="IK WIL EEN MEDEWERKER SPREKEN",
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
        visitor_query="I want a human",
    )
    assert "escalation" not in decision


def test_grounded_answer_without_force_or_marker_has_no_signal() -> None:
    _, _, decision = _compose_backend_managed_answer(
        "Ga naar Instellingen > Beveiliging.",
        _grounded_sources(),
        [_good_chunk()],
        "Hoe reset ik mijn wachtwoord?",
        helpdesk=True,
        visitor_query="Hoe reset ik mijn wachtwoord?",
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
        visitor_query="Hoe voeg ik een gebruiker toe?",
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
        visitor_query="Ik wil iemand spreken",
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
        visitor_query="IK WIL EEN MEDEWERKER SPREKEN",
    )
    assert decision.get("escalation") == {"appointment": True}
