"""Slice 6 of the one-chat-pipeline plan (docs/architecture/chat-quality-history-and-plan.md §7):
the internal-chat "Bronnen"/"Agent activiteit" footer and its history strip.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.services.answer_footer import render_answer_footer, strip_answer_footer_from_text
from app.services.chat_profile import ChatProfile

_SOURCES = [
    {"label": "1", "title": "VPN-instellingen", "url": "https://example.com/docs/vpn"},
]


def test_no_sources_means_no_footer():
    assert render_answer_footer(sources=[], kb_mode="strict", chunks_injected=3) == ""


def test_footer_lists_sources_and_agent_activity_in_dutch_by_default():
    footer = render_answer_footer(sources=_SOURCES, kb_mode="strict", chunks_injected=4, language=None)
    assert "**Bronnen**" in footer
    assert "[VPN-instellingen](https://example.com/docs/vpn)" in footer
    assert "**Agent activiteit**" in footer
    assert "- Modus: Strict, alleen kennisbank." in footer
    assert "- Kennisbank geraadpleegd: 4 fragmenten opgehaald." in footer


def test_footer_renders_in_english_for_an_explicit_english_turn():
    footer = render_answer_footer(sources=_SOURCES, kb_mode="open", chunks_injected=1, language="en")
    assert "**Sources**" in footer
    assert "**Agent activity**" in footer
    assert "- Mode: Open, knowledge base with fallback." in footer
    assert "- Knowledge base queried: 1 chunk retrieved." in footer


def test_footer_reports_sub_question_count_only_when_supplied():
    without = render_answer_footer(sources=_SOURCES, kb_mode="strict", chunks_injected=1)
    assert "Deelvragen" not in without

    with_sub_queries = render_answer_footer(
        sources=_SOURCES,
        kb_mode="strict",
        chunks_injected=1,
        sub_queries=["Hoe reset ik mijn wachtwoord?", "Hoe wijzig ik mijn e-mailadres?"],
    )
    assert "- Deelvragen: 2 apart gezocht." in with_sub_queries


def test_strip_removes_the_footer_from_an_earlier_assistant_turn():
    answer = (
        "Je kunt de VPN-instellingen aanpassen in het portaal.\n\n"
        "**Bronnen**\n- [VPN-instellingen](https://example.com/docs/vpn)\n\n"
        "**Agent activiteit**\n- Modus: Strict, alleen kennisbank."
    )
    assert strip_answer_footer_from_text(answer) == "Je kunt de VPN-instellingen aanpassen in het portaal."


def test_strip_leaves_prose_that_only_mentions_the_heading_words():
    text = "Zie het kopje Agent activiteit in de instellingenpagina voor meer opties."
    assert strip_answer_footer_from_text(text) == text


def test_strip_is_a_no_op_without_a_footer():
    text = "Je kunt de VPN-instellingen aanpassen in het portaal."
    assert strip_answer_footer_from_text(text) == text


def test_footer_is_stripped_from_retrieval_history_and_model_history():
    """The hook strips its own footer from earlier assistant turns before
    retrieval (deploy/litellm/klai_kb_request_context.py:77-83); this is the
    same strip, ported into portal's history builders, so a footer never
    enters a search query or the model's history.
    """
    from app.services.partner_chat import _augment_messages_with_system_prompt, _build_conversation_history

    footer_answer = (
        "Je kunt de VPN-instellingen aanpassen in het portaal.\n\n"
        "**Bronnen**\n- [VPN-instellingen](https://example.com/docs/vpn)\n\n"
        "**Agent activiteit**\n- Modus: Strict, alleen kennisbank."
    )
    stripped_answer = "Je kunt de VPN-instellingen aanpassen in het portaal."
    messages = [
        {"role": "user", "content": "Hoe wijzig ik mijn VPN?"},
        {"role": "assistant", "content": footer_answer},
        {"role": "user", "content": "En op mobiel?"},
    ]

    retrieval_history = _build_conversation_history(messages)
    assert retrieval_history[-1] == {"role": "assistant", "content": stripped_answer}

    augmented = _augment_messages_with_system_prompt(messages, "system prompt", response_language="nl")
    assistant_turn = next(m for m in augmented if m["role"] == "assistant")
    assert assistant_turn["content"] == stripped_answer


# ---------------------------------------------------------------------------
# Streaming acceptance tests: the footer is the LAST render step, appended
# after the answer and before [DONE], only for profile.surface == "internal".
# ---------------------------------------------------------------------------

_SINGLE_SOURCE_CITATION_CHUNKS = [
    {
        "title": "Privacy policy",
        "source_url": "https://getklai.com/docs/legal/privacy",
        "text": "Naam en e-mailadres staan in de privacy policy.",
    }
]
_SINGLE_TRUSTED_SOURCE = [{"label": "1", "title": "Privacy policy", "url": "https://getklai.com/docs/legal/privacy"}]


class _StreamResp:
    def __init__(self, events):
        self._events = events

    def raise_for_status(self):
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def aiter_lines(self):
        for event in self._events:
            yield "data: " + json.dumps(event)
        yield "data: [DONE]"


class _Client:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def stream(self, *_, **__):
        return _StreamResp(self._events)


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm"
    settings.litellm_master_key = "secret"
    return settings


def _content_frames(body: str) -> list[str]:
    frames = []
    for line in body.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        delta = json.loads(line[6:])["choices"][0]["delta"]
        if "content" in delta:
            frames.append(delta["content"])
    return frames


@pytest.mark.asyncio
async def test_held_internal_turn_with_sources_appends_footer_before_done(monkeypatch):
    from app.services.partner_chat import chat_completion_streaming

    events = [{"choices": [{"delta": {"content": "Naam 4(https://getklai.com/docs/legal/privacy)."}}]}]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Welke gegevens staan in de privacy policy?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_chunks=_SINGLE_SOURCE_CITATION_CHUNKS,
        trusted_sources=_SINGLE_TRUSTED_SOURCE,
        citation_output="markers",
        profile=ChatProfile(surface="internal", kb_mode="strict"),
    ):
        chunks.append(chunk)

    body = b"".join(chunks).decode()
    final_content = _content_frames(body)[-1]
    assert final_content.startswith("Naam.")
    assert "**Bronnen**\n- [Privacy policy](https://getklai.com/docs/legal/privacy)" in final_content
    assert "**Agent activiteit**\n- Modus: Strict, alleen kennisbank." in final_content
    # Structured sources frame for the widget stays exactly as composed —
    # the footer does not change that contract.
    assert '"sources": [{"label": "1", "title": "Privacy policy"' in body
    assert body.index("Agent activiteit") < body.index("[DONE]")


@pytest.mark.asyncio
async def test_live_internal_turn_with_sources_appends_footer_as_extra_delta(monkeypatch):
    from app.services.partner_chat import chat_completion_streaming

    events = [{"choices": [{"delta": {"content": "Naam 4(https://getklai.com/docs/legal/privacy)."}}]}]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Welke gegevens staan in de privacy policy?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_chunks=_SINGLE_SOURCE_CITATION_CHUNKS,
        trusted_sources=_SINGLE_TRUSTED_SOURCE,
        citation_output="markers",
        profile=ChatProfile(surface="internal", kb_mode="open", stream_live=True),
    ):
        chunks.append(chunk)

    body = b"".join(chunks).decode()
    content_frames = _content_frames(body)
    # The live text already went out token-by-token, unrewritten (the raw
    # model marker, not the composed "Naam."); the footer is new content the
    # caller has not seen, so it is one more delta, never a resend of the
    # answer.
    assert "".join(content_frames[:-1]) == "Naam 4(https://getklai.com/docs/legal/privacy)."
    footer_frame = content_frames[-1]
    assert footer_frame.startswith("\n\n**Bronnen**")
    assert "[Privacy policy](https://getklai.com/docs/legal/privacy)" in footer_frame
    assert "**Agent activiteit**\n- Modus: Open, kennisbank met fallback." in footer_frame
    assert body.index("Agent activiteit") < body.index("[DONE]")


@pytest.mark.asyncio
async def test_internal_turn_without_sources_gets_no_footer(monkeypatch):
    from app.services.partner_chat import chat_completion_streaming

    events = [{"choices": [{"delta": {"content": "Klai ondersteunt SIP-trunking voor elk plan."}}]}]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Ondersteunt Klai SIP-trunking?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_output="markers",
        profile=ChatProfile(surface="internal", kb_mode="open"),
    ):
        chunks.append(chunk)

    body = b"".join(chunks).decode()
    assert "Bronnen" not in body
    assert "Agent activiteit" not in body


@pytest.mark.asyncio
async def test_widget_stream_with_sources_is_unaffected_by_the_internal_footer(monkeypatch):
    """Byte-for-byte guard: a widget turn with the same sources must not gain
    a footer — the footer is internal-only."""
    from app.services.partner_chat import chat_completion_streaming

    events = [{"choices": [{"delta": {"content": "Naam 4(https://getklai.com/docs/legal/privacy)."}}]}]
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _Client(events))

    chunks = []
    async for chunk in chat_completion_streaming(
        messages=[{"role": "user", "content": "Welke gegevens staan in de privacy policy?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_chunks=_SINGLE_SOURCE_CITATION_CHUNKS,
        trusted_sources=_SINGLE_TRUSTED_SOURCE,
        citation_output="markers",
        profile=ChatProfile(surface="widget"),
    ):
        chunks.append(chunk)

    body = b"".join(chunks).decode()
    assert _content_frames(body) == ["Naam."]
    assert "**Bronnen**" not in body
    assert "Agent activiteit" not in body


@pytest.mark.asyncio
async def test_non_streaming_internal_turn_with_sources_appends_footer_to_message_content(monkeypatch):
    from app.services.partner_chat import chat_completion_non_streaming

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Naam 4(https://getklai.com/docs/legal/privacy)."}}]}

    class _AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            return _Response()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _AsyncClient())

    result = await chat_completion_non_streaming(
        messages=[{"role": "user", "content": "Welke gegevens staan in de privacy policy?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_chunks=_SINGLE_SOURCE_CITATION_CHUNKS,
        trusted_sources=_SINGLE_TRUSTED_SOURCE,
        citation_output="markers",
        profile=ChatProfile(surface="internal", kb_mode="strict"),
    )

    content = result["choices"][0]["message"]["content"]
    assert content.startswith("Naam.")
    assert "**Bronnen**\n- [Privacy policy](https://getklai.com/docs/legal/privacy)" in content
    assert "**Agent activiteit**\n- Modus: Strict, alleen kennisbank." in content


@pytest.mark.asyncio
async def test_non_streaming_widget_turn_is_unaffected_by_the_internal_footer(monkeypatch):
    from app.services.partner_chat import chat_completion_non_streaming

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Naam 4(https://getklai.com/docs/legal/privacy)."}}]}

    class _AsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            return _Response()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _AsyncClient())

    result = await chat_completion_non_streaming(
        messages=[{"role": "user", "content": "Welke gegevens staan in de privacy policy?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_chunks=_SINGLE_SOURCE_CITATION_CHUNKS,
        trusted_sources=_SINGLE_TRUSTED_SOURCE,
        citation_output="markers",
    )

    assert result["choices"][0]["message"]["content"] == "Naam."
