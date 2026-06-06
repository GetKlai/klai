"""Shared chat-mode contract for the regular KB chat path."""

from __future__ import annotations

from typing import Literal

ChatRetrievalPromptMode = Literal[
    "general",
    "open_kb",
    "strict_kb",
    "strict_no_kb",
    "open_unavailable",
    "strict_unavailable",
]

PROMPT_MODES = frozenset(
    {
        "general",
        "open_kb",
        "strict_kb",
        "strict_no_kb",
        "open_unavailable",
        "strict_unavailable",
    }
)
STRICT_PROMPT_MODES = frozenset(
    {
        "strict_kb",
        "strict_no_kb",
        "strict_unavailable",
    }
)
RETRIEVAL_PROMPT_MODES = frozenset(
    {
        "open_kb",
        "strict_kb",
    }
)
UNAVAILABLE_PROMPT_MODES = frozenset(
    {
        "open_unavailable",
        "strict_unavailable",
    }
)


def prompt_mode_is_strict(prompt_mode: ChatRetrievalPromptMode) -> bool:
    return prompt_mode in STRICT_PROMPT_MODES


def prompt_mode_is_known(prompt_mode: object) -> bool:
    return prompt_mode in PROMPT_MODES


def prompt_mode_should_retrieve(prompt_mode: ChatRetrievalPromptMode) -> bool:
    return prompt_mode in RETRIEVAL_PROMPT_MODES


def prompt_mode_is_unavailable(prompt_mode: ChatRetrievalPromptMode) -> bool:
    return prompt_mode in UNAVAILABLE_PROMPT_MODES


def prompt_mode_answer_mode(prompt_mode: ChatRetrievalPromptMode) -> str:
    return "strict" if prompt_mode_is_strict(prompt_mode) else "open"
