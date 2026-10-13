"""Whether this conversation already had its one clarifying question.

The selection step writes the question (passage_selection.py); this module
only tells the flow whether to ask it, once per conversation.
"""

from __future__ import annotations

from app.services.answer_footer import strip_answer_footer_from_text


def _messages(messages: list[dict]) -> list[dict]:
    """User and assistant turns as text, content parts joined, an internal answer footer removed."""
    turns = []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        if role in ("user", "assistant") and isinstance(content, str):
            turns.append(
                {"role": role, "content": strip_answer_footer_from_text(content) if role == "assistant" else content}
            )
    return turns


def already_asked(messages: list[dict]) -> bool:
    """A reply to the visitor already ended on a question; an opening greeting is no reply.

    One question per conversation. A low-quality question suppresses the
    visitor's engagement with later, better ones (Zou et al. 2023), and there
    is no way to tell that the first question did not land: a visitor who never
    named a device was asked for it twice in one real conversation.
    """
    conversation = _messages(messages)
    first_user = next((index for index, m in enumerate(conversation) if m["role"] == "user"), len(conversation))
    return any(
        m["role"] == "assistant" and m["content"].rstrip(" \n*_)\"'").endswith("?") for m in conversation[first_user:-1]
    )
