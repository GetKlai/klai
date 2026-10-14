"""Server-sent event frames of the chat stream: content, tool calls, sources, activity and errors."""

from __future__ import annotations

import json


def _sse_content_delta(text: str) -> bytes:
    payload = {"choices": [{"delta": {"content": text}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _sse_tool_calls_delta(tool_calls: list[dict]) -> bytes:
    """Forward a tool_calls delta unbuffered — never routed through the text buffer."""
    payload = {"choices": [{"index": 0, "delta": {"tool_calls": tool_calls}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _sse_sources_delta(sources: list[dict[str, str]]) -> bytes:
    payload = {"choices": [{"delta": {"sources": sources}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _sse_activity_delta(activity: list[dict[str, str | int]]) -> bytes:
    payload = {"choices": [{"delta": {"activity": activity}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _sse_broad_mode_delta(mode: str) -> bytes:
    payload = {"choices": [{"delta": {"broad_mode": mode}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _sse_language_delta(language: str) -> bytes:
    payload = {"choices": [{"delta": {"language": language}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _sse_escalation_delta(escalation: dict[str, bool]) -> bytes:
    payload = {"choices": [{"delta": {"escalation": escalation}}]}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _sse_error_frame(message: str) -> bytes:
    """OpenAI-compatible SSE error frame.

    Once a StreamingResponse has started, an upstream failure cannot become an
    HTTP 502, so the clean contract is an error event on the stream followed by
    [DONE] — the client sees an explicit error instead of a truncated/broken SSE.
    """
    payload = {"error": {"type": "upstream_error", "message": message}}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
