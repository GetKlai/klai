"""Portal-api adapter for the shared Klai LLM safety policy.

This module intentionally stays thin: rollout mode, provider calls, and richer
telemetry can be added here without re-implementing policy in every chat path.
"""

from __future__ import annotations

from typing import Any

from klai_llm_safety import SafetyDecision, SafetyPhase, SafetyRequest, SafetySurface, check_text, refusal_message


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def last_user_message(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _message_text(message)
    return ""


def check_widget_or_partner_input(
    messages: list[dict],
    *,
    surface: SafetySurface = SafetySurface.WIDGET,
) -> SafetyDecision:
    # Input safety scans ONLY the latest user turn. Scanning the whole
    # conversation (including assistant turns) meant one earlier grey-area
    # message poisoned every later question in the same chat. Assistant
    # output is covered by the OUTPUT gate, not the INPUT gate.
    user_query = last_user_message(messages)
    return check_text(
        SafetyRequest(
            text=user_query,
            phase=SafetyPhase.INPUT,
            surface=surface,
            locale_hint=user_query,
        )
    )


def check_model_output(
    text: str,
    *,
    query: str = "",
    surface: SafetySurface = SafetySurface.WIDGET,
) -> SafetyDecision:
    return check_text(
        SafetyRequest(
            text=text,
            phase=SafetyPhase.OUTPUT,
            surface=surface,
            locale_hint=query,
        )
    )


def check_context_text(
    text: str,
    *,
    query: str = "",
    surface: SafetySurface = SafetySurface.WIDGET,
) -> SafetyDecision:
    return check_text(
        SafetyRequest(
            text=text,
            phase=SafetyPhase.CONTEXT,
            surface=surface,
            locale_hint=query,
        )
    )


def safe_refusal_text(query: str = "", reason: str = "") -> str:
    return refusal_message(query, reason)
