"""A widget reply renders to exactly the bytes and keys the two renderers it replaced produced.

The expected frames and bodies below were captured from the removed code
(chat_completion_streaming / chat_completion_non_streaming on a widget turn,
off_topic_stream / off_topic_response for a reply decided in code) with the
same synthetic text, and written out by hand. Frame bytes are compared as
bytes; a body is compared key for key (the old bodies kept LiteLLM's key order,
which JSON readers ignore).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from fastapi import HTTPException
from helpers import FakeKB, FakeResult, make_partner_auth

from app.api.partner import ChatCompletionsRequest, chat_completions
from app.services.partner_chat import KnowledgeTurn
from app.services.widget_reply import AnswerUpstreamError, Reply, error_frames, reply_body, reply_frames
from app.services.widget_write import write_reply

ANSWER = "Open de instellingen en kies Doorschakelen."
SOURCE = {"label": "1", "title": "Voorbeeld artikel", "url": "https://help.example.com/doorschakelen", "origin": "kb"}
REFUSAL = (
    "Dit vind ik niet terug in onze helpartikelen. Wil je het zeker weten, plan dan een afspraak met een "
    "medewerker. Die helpt je persoonlijk verder."
)
DONE = b"data: [DONE]\n\n"
LANGUAGE_NL = b'data: {"choices": [{"delta": {"language": "nl"}}]}\n\n'
APPOINTMENT = b'data: {"choices": [{"delta": {"escalation": {"appointment": true}}}]}\n\n'
RETRIEVED = (
    b'data: {"choices": [{"delta": {"activity": [{"step": "knowledge_retrieved", "label": "Kennisbank geraadpleegd", '
    b'"detail": "1 passages gevonden", "count": 1}]}}]}\n\n'
)
SOURCES = (
    b'data: {"choices": [{"delta": {"sources": [{"label": "1", "title": "Voorbeeld artikel", '
    b'"url": "https://help.example.com/doorschakelen", "origin": "kb"}]}}]}\n\n'
)
ATTACHED = (
    b'data: {"choices": [{"delta": {"activity": [{"step": "sources_attached", "label": "Bronnen gekoppeld", '
    b'"detail": "1 bronnen beschikbaar", "count": 1}]}}]}\n\n'
)


def _content(text: str) -> bytes:
    return f'data: {{"choices": [{{"delta": {{"content": "{text}"}}}}]}}\n\n'.encode()


def _written(**overrides: Any) -> Reply:
    fields: dict[str, Any] = {
        "text": ANSWER,
        "sources": [dict(SOURCE)],
        "signals": {},
        "language": "nl",
        "turn": KnowledgeTurn(),
        "passages": 1,
        "completion": {
            "id": "chatcmpl-abc",
            "model": "klai-primary",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    }
    return Reply(**{**fields, **overrides})


def _fixed(**overrides: Any) -> Reply:
    fields: dict[str, Any] = {
        "text": "Een vaste tekst.",
        "sources": [],
        "signals": {},
        "language": "nl",
        "turn": KnowledgeTurn(),
        "appointment": True,
    }
    return Reply(**{**fields, **overrides})


def test_a_written_reply_with_a_source_and_an_appointment_streams_in_the_old_frame_order():
    assert reply_frames(_written(appointment=True)) == [
        LANGUAGE_NL,
        APPOINTMENT,
        RETRIEVED,
        SOURCES,
        ATTACHED,
        _content(ANSWER),
        DONE,
    ]


def test_a_written_reply_with_a_source_streams_without_the_appointment_frame():
    assert reply_frames(_written()) == [LANGUAGE_NL, RETRIEVED, SOURCES, ATTACHED, _content(ANSWER), DONE]


def test_a_refusal_that_offers_a_broader_search_streams_the_signals_before_its_text():
    reply = _written(text=REFUSAL, sources=[], passages=0, appointment=True, broad_mode="offer")
    assert reply_frames(reply) == [
        LANGUAGE_NL,
        b'data: {"choices": [{"delta": {"broad_mode": "offer"}}]}\n\n',
        APPOINTMENT,
        _content(REFUSAL),
        DONE,
    ]


def test_a_reply_asked_without_sources_streams_no_source_frames():
    assert reply_frames(_written(), include_sources=False) == [LANGUAGE_NL, RETRIEVED, _content(ANSWER), DONE]


def test_a_reply_decided_in_code_streams_language_appointment_text() -> None:
    assert reply_frames(_fixed()) == [LANGUAGE_NL, APPOINTMENT, _content("Een vaste tekst."), DONE]


def test_a_question_decided_in_code_streams_no_appointment_and_no_language_when_there_is_none() -> None:
    assert reply_frames(_fixed(appointment=False, language=None)) == [_content("Een vaste tekst."), DONE]


def test_a_written_reply_renders_the_completion_body_it_came_with():
    assert reply_body(_written(appointment=True), model="klai-primary") == {
        "id": "chatcmpl-abc",
        "object": "chat.completion",
        "model": "klai-primary",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": ANSWER,
                    "language": "nl",
                    "sources": [SOURCE],
                    "escalation": {"appointment": True},
                },
                "finish_reason": "stop",
            }
        ],
    }


def test_a_refusal_body_carries_the_broad_mode_offer_and_no_sources() -> None:
    message = reply_body(
        _written(text=REFUSAL, sources=[], passages=0, appointment=True, broad_mode="offer"), model="klai-primary"
    )["choices"][0]["message"]
    assert message == {
        "role": "assistant",
        "content": REFUSAL,
        "language": "nl",
        "sources": [],
        "broad_mode": "offer",
        "escalation": {"appointment": True},
    }


def test_a_reply_decided_in_code_renders_the_body_the_referral_renderer_produced() -> None:
    assert reply_body(_fixed(), model="klai-primary") == {
        "id": "chatcmpl-off-topic",
        "object": "chat.completion",
        "model": "klai-primary",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Een vaste tekst.",
                    "sources": [],
                    "escalation": {"appointment": True},
                    "language": "nl",
                },
                "finish_reason": "stop",
            }
        ],
    }
    assert reply_body(_fixed(appointment=False, language=None), model="klai-primary")["choices"][0]["message"] == {
        "role": "assistant",
        "content": "Een vaste tekst.",
        "sources": [],
    }


def test_a_body_asked_without_sources_has_no_sources_key() -> None:
    message = reply_body(_written(), model="klai-primary", include_sources=False)["choices"][0]["message"]
    assert "sources" not in message


def test_an_upstream_failure_streams_the_language_then_the_error() -> None:
    assert error_frames(AnswerUpstreamError("Chat service unavailable", "nl")) == [
        LANGUAGE_NL,
        b'data: {"error": {"type": "upstream_error", "message": "Chat service unavailable"}}\n\n',
        DONE,
    ]


async def _chat_completions_failing(*, stream: bool):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[FakeKB(id=10, name="KB", slug="kb-a", org_id=42)]))
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_901"
    request = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hoe reset ik mijn wachtwoord?"}], stream=stream
    )
    http_request = MagicMock(headers={}, client=None)
    with (
        patch("app.api.partner._widget_support_mode_enabled", new=AsyncMock(return_value=True)),
        patch("app.api.partner._widget_page_context_enabled", new=AsyncMock(return_value=False)),
        patch("app.api.partner._widget_tone_register", new=AsyncMock(return_value="restrained")),
        patch(
            "app.api.partner.widget_turn",
            new=AsyncMock(side_effect=AnswerUpstreamError("Chat service error", "nl")),
        ),
        patch("app.api.partner.asyncio"),
    ):
        return await chat_completions(request=request, http_request=http_request, auth=auth, db=db)


@pytest.mark.asyncio
async def test_a_streamed_request_gets_the_upstream_failure_as_frames_and_a_plain_one_as_a_502() -> None:
    response = await _chat_completions_failing(stream=True)
    assert [chunk async for chunk in response.body_iterator] == error_frames(
        AnswerUpstreamError("Chat service error", "nl")
    )

    with pytest.raises(HTTPException) as failure:
        await _chat_completions_failing(stream=False)
    assert failure.value.status_code == 502
    assert failure.value.detail["error"]["type"] == "upstream_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (httpx.ConnectError("all attempts failed"), "Chat service unavailable"),
        (httpx.Response(503), "Chat service error"),
    ],
    ids=["unreachable", "status"],
)
async def test_the_writer_names_an_unreachable_or_failing_answer_model_with_the_turn_language(failure, message) -> None:
    settings = MagicMock(litellm_base_url="http://litellm:4000", litellm_master_key="key")
    with respx.mock() as router:
        router.post("http://litellm:4000/v1/chat/completions").mock(
            **({"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure})
        )
        with pytest.raises(AnswerUpstreamError) as raised:
            await write_reply(
                messages=[{"role": "user", "content": "Hoe reset ik mijn wachtwoord?"}],
                model="klai-primary",
                temperature=0.2,
                system_prompt="sys",
                settings=settings,
            )
    assert (raised.value.status_code, raised.value.message, raised.value.language) == (502, message, "nl")
