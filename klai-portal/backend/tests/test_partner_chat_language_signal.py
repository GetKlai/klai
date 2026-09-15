"""The response language reaches the widget on every turn, in both routes.

The backend already decides a per-turn ``LanguageDecision`` to steer the
system prompt (``resolve_conversation_language`` in ``_augment_messages_with_
system_prompt``). This signal must ride the SAME decision to the client: a
streaming ``delta.language`` frame before the content, and a non-streaming
``message.language`` field, so the widget never shows English buttons under a
Dutch answer because a second detector call disagreed with the first.

Covered seams:

* ``chat_completion_streaming`` — one ``delta.language`` frame per turn,
  before any content, for both an established switch and an abstained
  decision (field absent).
* ``chat_completion_non_streaming`` — ``message.language`` alongside the
  existing ``message.broad_mode`` / ``message.escalation`` fields.
* An ORDINARY, non-refused, non-broad-mode turn carries the signal too — this
  is the case that breaks if the language frame reuses the broad_mode
  emission point, which only fires on the refusal path.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.services import partner_chat

# A single unambiguous English sentence establishes "en" on the very first
# turn (see klai-chat-prompts language.py: establishing when nothing is set
# is cheap, any turn that passes the gate and confidence tiers establishes).
EN_1 = "How do I invite a new user to my team and set their permissions?"


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm:4000"
    settings.litellm_master_key = "key"
    return settings


def _streaming_client(content: str):
    events = [{"choices": [{"delta": {"content": content}}]}]

    class _StreamResp:
        def raise_for_status(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def aiter_lines(self):
            for event in events:
                yield "data: " + json.dumps(event)
            yield "data: [DONE]"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *_, **__):
            return _StreamResp()

    return _Client()


def _non_streaming_client(content: str):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": content}}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            return _Resp()

    return _Client()


def _sse_deltas(body: bytes) -> list[dict]:
    out = []
    for line in body.decode().splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        out.append(json.loads(line[6:])["choices"][0]["delta"])
    return out


@pytest.mark.asyncio
async def test_streaming_conversation_switching_to_english_emits_one_language_frame_before_content(monkeypatch):
    """A turn that decides English carries exactly one delta.language == 'en' frame, before the content."""
    monkeypatch.setattr(
        "app.services.partner_chat.httpx.AsyncClient", lambda timeout: _streaming_client("Sure, here you go.")
    )

    chunks = []
    async for chunk in partner_chat.chat_completion_streaming(
        messages=[{"role": "user", "content": EN_1}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
    ):
        chunks.append(chunk)

    deltas = _sse_deltas(b"".join(chunks))
    language_deltas = [d for d in deltas if "language" in d]
    assert language_deltas == [{"language": "en"}]

    content_index = next(i for i, d in enumerate(deltas) if "content" in d)
    language_index = next(i for i, d in enumerate(deltas) if "language" in d)
    assert language_index < content_index


@pytest.mark.asyncio
async def test_non_streaming_conversation_switching_to_english_sets_message_language(monkeypatch):
    """The non-streaming twin of the above: message.language == 'en'."""
    monkeypatch.setattr(
        "app.services.partner_chat.httpx.AsyncClient", lambda timeout: _non_streaming_client("Sure, here you go.")
    )

    body = await partner_chat.chat_completion_non_streaming(
        messages=[{"role": "user", "content": EN_1}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
    )

    assert body["choices"][0]["message"]["language"] == "en"


@pytest.mark.asyncio
async def test_streaming_abstained_language_decision_omits_the_field(monkeypatch):
    """No user turns -> no.evidence -> the language field is entirely absent, not null."""
    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _streaming_client("Antwoord."))

    chunks = []
    async for chunk in partner_chat.chat_completion_streaming(
        messages=[],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
    ):
        chunks.append(chunk)

    deltas = _sse_deltas(b"".join(chunks))
    assert not any("language" in d for d in deltas)


@pytest.mark.asyncio
async def test_non_streaming_abstained_language_decision_omits_the_field(monkeypatch):
    """Non-streaming twin: no 'language' key anywhere in the message, not None."""
    monkeypatch.setattr(
        "app.services.partner_chat.httpx.AsyncClient", lambda timeout: _non_streaming_client("Antwoord.")
    )

    body = await partner_chat.chat_completion_non_streaming(
        messages=[],
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
    )

    assert "language" not in body["choices"][0]["message"]


@pytest.mark.asyncio
async def test_streaming_ordinary_answered_turn_also_carries_the_language_signal(monkeypatch):
    """An ordinary, non-refused, non-broad-mode answer carries delta.language too.

    This is the case that silently breaks if the emission point is copied from
    the broad_mode frame (fires only on the refusal/broad_mode decision path).
    Uses citation_output="markers" with real sources so the composer produces
    a normal cited answer, not a refusal.
    """
    monkeypatch.setattr(
        "app.services.partner_chat.httpx.AsyncClient",
        lambda timeout: _streaming_client("Klai is steward-owned and mission-led."),
    )

    chunks = []
    async for chunk in partner_chat.chat_completion_streaming(
        # The language decision is taken on `messages` (English -> "en");
        # `source_query` is the separate KB-matching query the citation
        # composer scores against, mirroring the topic of the mocked answer.
        messages=[{"role": "user", "content": EN_1}],
        source_query="Wat is Klai precies en hoe werkt het?",
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_chunks=[
            {
                "title": "Steward ownership",
                "source_url": "https://www.getklai.com/docs/company/steward-ownership",
                "text": "Klai is steward-owned and mission-led.",
            }
        ],
        trusted_sources=[
            {"label": "1", "title": "Steward ownership", "url": "https://getklai.com/docs/company/steward-ownership"}
        ],
        citation_output="markers",
    ):
        chunks.append(chunk)

    body = b"".join(chunks)
    deltas = _sse_deltas(body)
    # Sanity: this really is the ordinary cited-answer path, not a refusal/broad_mode.
    assert "".join(d.get("content", "") for d in deltas) == "Klai is steward-owned and mission-led."
    assert not any("broad_mode" in d for d in deltas)
    assert {"language": "en"} in deltas


@pytest.mark.asyncio
async def test_non_streaming_ordinary_answered_turn_also_carries_the_language_signal(monkeypatch):
    """Non-streaming twin of the ordinary-turn regression above."""
    monkeypatch.setattr(
        "app.services.partner_chat.httpx.AsyncClient",
        lambda timeout: _non_streaming_client("Klai is steward-owned and mission-led."),
    )

    body = await partner_chat.chat_completion_non_streaming(
        messages=[{"role": "user", "content": EN_1}],
        source_query="Wat is Klai precies en hoe werkt het?",
        model="klai-primary",
        temperature=0.7,
        system_prompt="prompt",
        settings=_settings(),
        citation_chunks=[
            {
                "title": "Steward ownership",
                "source_url": "https://www.getklai.com/docs/company/steward-ownership",
                "text": "Klai is steward-owned and mission-led.",
            }
        ],
        trusted_sources=[
            {"label": "1", "title": "Steward ownership", "url": "https://getklai.com/docs/company/steward-ownership"}
        ],
        citation_output="markers",
    )

    message = body["choices"][0]["message"]
    assert message["content"] == "Klai is steward-owned and mission-led."
    assert "broad_mode" not in message
    assert message["language"] == "en"
