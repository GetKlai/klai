"""One chat reply, and the one place that renders it for the wire.

The widget's turn (widget_turn.py) and the internal chat's turn
(internal_turn.py) end in a ``Reply`` whether the text was written by the
model or decided in code. This module turns a ``Reply`` into the two shapes
the contract allows: the non-streaming completion body and the SSE frames,
for a held reply all at once and for a streamed one as the model writes it.
Nothing else renders a reply.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status

from app.services.chat_safety import _appointment_escalation
from app.services.chat_sse import (
    _sse_activity_delta,
    _sse_broad_mode_delta,
    _sse_content_delta,
    _sse_error_frame,
    _sse_escalation_delta,
    _sse_language_delta,
    _sse_sources_delta,
    _sse_tool_calls_delta,
)
from app.services.retrieval_context import KnowledgeTurn

_DONE = b"data: [DONE]\n\n"
# OpenAI clients read this finish reason as "run the tools now".
_RUN_THE_TOOLS = b'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}\n\n'


@dataclass
class Reply:
    """One reply, decided in code or written, with what the caller needs to send and record it."""

    # What the reader gets in one piece; on a streamed reply, what follows the
    # deltas once it settled: the part not yet seen and the footer.
    text: str
    sources: list[dict]
    signals: dict[str, Any]
    language: str | None
    # Carries the deferred grounding check the record waits for.
    turn: KnowledgeTurn
    appointment: bool = False
    broad_mode: str | None = None
    # How many passages the writer read, for the "passages found" activity frame.
    passages: int = 0
    # The answer call's id, model and usage as LiteLLM returned them; empty on a reply decided in code.
    completion: dict[str, Any] = field(default_factory=dict)
    # The employee's own tools the answer model called (internal only); the client runs them.
    # On a streamed reply these are the call deltas as they were already sent.
    tool_calls: list[dict] | None = None
    # Live web results the writer read beside the passages (internal Open and general), for the activity frame.
    web_results: int = 0
    # A streamed reply (internal Open and general): the model's text, and its
    # tool-call deltas, as the reader may see them now. Every field above
    # settles in place once these are exhausted (widget_write.write_reply).
    deltas: AsyncIterator[str | list[dict]] | None = None


class AnswerUpstreamError(HTTPException):
    """The answer model could not be reached, or refused the call: a 502, or an error frame once streaming."""

    def __init__(self, message: str, language: str | None) -> None:
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": {"type": "upstream_error", "message": message}},
        )
        self.message = message
        self.language = language


def reply_body(reply: Reply, *, model: str, include_sources: bool = True) -> dict[str, Any]:
    """The non-streaming completion body of a reply."""
    message: dict[str, Any] = {"role": "assistant", "content": reply.text}
    if reply.language is not None:
        message["language"] = reply.language
    if include_sources:
        message["sources"] = reply.sources
    if reply.broad_mode in ("offer", "answer"):
        message["broad_mode"] = reply.broad_mode
    if reply.appointment:
        message["escalation"] = _appointment_escalation()
    if reply.tool_calls:
        message["tool_calls"] = reply.tool_calls
    completion = dict(reply.completion)
    finish_reason = "tool_calls" if reply.tool_calls else completion.pop("finish_reason", "stop")
    completion.pop("finish_reason", None)
    return {
        # Kept from the renderer a reply decided in code used before this one.
        "id": "chatcmpl-off-topic",
        "object": "chat.completion",
        "model": model,
        **completion,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }


def reply_frames(reply: Reply, *, include_sources: bool = True) -> list[bytes]:
    """The SSE frames of a reply: the signals first, so the widget can label the message before its text."""
    frames: list[bytes] = []
    if reply.language is not None:
        frames.append(_sse_language_delta(reply.language))
    if reply.broad_mode in ("offer", "answer"):
        frames.append(_sse_broad_mode_delta(reply.broad_mode))
    if reply.appointment:
        frames.append(_sse_escalation_delta(_appointment_escalation()))
    if reply.tool_calls:
        # A streamed tool call carries its position; the completion body's calls do not.
        frames.append(_sse_tool_calls_delta([{"index": i, **call} for i, call in enumerate(reply.tool_calls)]))
        if not reply.text:
            return [*frames, _RUN_THE_TOOLS, _DONE]
    return [*frames, *_closing_frames(reply, include_sources=include_sources)]


async def streamed_frames(reply: Reply, *, include_sources: bool = True) -> AsyncIterator[bytes]:
    """The SSE frames of a streamed reply: its language, the model's deltas as they arrive, then how it ends.

    Once the stream has started a failure of the answer model cannot become a
    502, so it ends the stream as an error frame. A turn that only called a
    tool ends on "run the tools now" without a composed text after it.
    """
    assert reply.deltas is not None, "a held reply has no deltas; reply_frames renders it"
    if reply.language is not None:
        yield _sse_language_delta(reply.language)
    try:
        async for delta in reply.deltas:
            yield _sse_content_delta(delta) if isinstance(delta, str) else _sse_tool_calls_delta(delta)
    except AnswerUpstreamError as error:
        yield _sse_error_frame(error.message)
        yield _DONE
        return
    if reply.tool_calls and not reply.text:
        yield _RUN_THE_TOOLS
        yield _DONE
        return
    for frame in _closing_frames(reply, include_sources=include_sources):
        yield frame


def _closing_frames(reply: Reply, *, include_sources: bool) -> list[bytes]:
    """What ends every reply: what was read, the sources, the text, and DONE."""
    frames: list[bytes] = []
    if reply.passages:
        frames.append(
            _sse_activity_delta(
                [
                    {
                        "step": "knowledge_retrieved",
                        "label": "Kennisbank geraadpleegd",
                        "detail": f"{reply.passages} passages gevonden",
                        "count": reply.passages,
                    }
                ]
            )
        )
    if reply.web_results:
        frames.append(
            _sse_activity_delta(
                [
                    {
                        "step": "web_searched",
                        "label": "Web doorzocht",
                        "detail": f"{reply.web_results} resultaten gevonden",
                        "count": reply.web_results,
                    }
                ]
            )
        )
    if reply.sources and include_sources:
        frames.append(_sse_sources_delta(reply.sources))
        frames.append(
            _sse_activity_delta(
                [
                    {
                        "step": "sources_attached",
                        "label": "Bronnen gekoppeld",
                        "detail": f"{len(reply.sources)} bronnen beschikbaar",
                        "count": len(reply.sources),
                    }
                ]
            )
        )
    frames.append(_sse_content_delta(reply.text))
    if reply.tool_calls:
        # Text beside a tool call still ends on "run the tools now"; without
        # it an OpenAI client shows the text and never runs the tool.
        frames.append(_RUN_THE_TOOLS)
    frames.append(_DONE)
    return frames


def error_frames(error: AnswerUpstreamError) -> list[bytes]:
    """What a streamed request gets when the answer model failed: the language the turn committed to, then the error."""
    frames = [_sse_language_delta(error.language)] if error.language is not None else []
    return [*frames, _sse_error_frame(error.message), _DONE]
