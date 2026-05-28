"""Retrieval-api adapter for shared LLM safety policy."""

from __future__ import annotations

from typing import Any

from klai_llm_safety import SafetyDecision, SafetyPhase, SafetyRequest, SafetySurface, check_text


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def conversation_text(messages: list[dict]) -> str:
    return "\n".join(
        _message_text(message)
        for message in messages
        if message.get("role") in {"user", "assistant"}
    )


def check_coreference_input(query: str, history: list[dict]) -> SafetyDecision:
    text = "\n".join(part for part in (conversation_text(history), query) if part)
    return check_text(
        SafetyRequest(
            text=text,
            phase=SafetyPhase.INPUT,
            surface=SafetySurface.RETRIEVAL_COREFERENCE,
            locale_hint=query,
        )
    )


def check_coreference_output(text: str, *, query: str) -> SafetyDecision:
    return check_text(
        SafetyRequest(
            text=text,
            phase=SafetyPhase.OUTPUT,
            surface=SafetySurface.RETRIEVAL_COREFERENCE,
            locale_hint=query,
        )
    )


def check_synthesis_context(text: str, *, query: str) -> SafetyDecision:
    return check_text(
        SafetyRequest(
            text=text,
            phase=SafetyPhase.CONTEXT,
            surface=SafetySurface.RETRIEVAL_SYNTHESIS,
            locale_hint=query,
        )
    )
