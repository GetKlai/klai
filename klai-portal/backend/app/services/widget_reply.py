"""One chat reply, and the one place that renders it for the wire.

The widget's turn (widget_turn.py) and the internal chat's turn
(internal_turn.py) end in a ``Reply`` whether the text was written by the
model or decided in code. This module turns a ``Reply`` into the two shapes
the contract allows: the non-streaming completion body and the SSE frames.
Nothing else renders such a reply; only an internal Open or general answer,
streamed live, has its own frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, status

from app.services.partner_chat import (
    KnowledgeTurn,
    _appointment_escalation,
    _sse_activity_delta,
    _sse_broad_mode_delta,
    _sse_content_delta,
    _sse_error_frame,
    _sse_escalation_delta,
    _sse_language_delta,
    _sse_sources_delta,
    _sse_tool_calls_delta,
)

_DONE = b"data: [DONE]\n\n"


@dataclass
class Reply:
    """One reply, decided in code or written, with what the caller needs to send and record it."""

    text: str
    sources: list[dict]
    # The route the turn took; for a written reply the composer's decision with it.
    decision: dict[str, Any]
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
    # The employee's own tools the answer model called (internal Strict only); the client runs them.
    tool_calls: list[dict] | None = None


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
    return {
        # Kept from the renderer a reply decided in code used before this one.
        "id": "chatcmpl-off-topic",
        "object": "chat.completion",
        "model": model,
        **reply.completion,
        "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if reply.tool_calls else "stop"}],
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
            # OpenAI clients read this finish reason as "run the tools now".
            return [
                *frames,
                b'data: {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}\n\n',
                _DONE,
            ]
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
    frames.append(_DONE)
    return frames


def error_frames(error: AnswerUpstreamError) -> list[bytes]:
    """What a streamed request gets when the answer model failed: the language the turn committed to, then the error."""
    frames = [_sse_language_delta(error.language)] if error.language is not None else []
    return [*frames, _sse_error_frame(error.message), _DONE]
