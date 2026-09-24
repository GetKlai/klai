"""Detect a turn that carries the user's own content: an attachment, a
converted PDF, or an explicit question about the visible conversation.

Ported from the LiteLLM hook's user-provided-content rule
(``deploy/litellm/klai_kb_answer_policy.py``: ``USER_ATTACHMENT_PART_TYPES``,
``_has_user_attachment_context``, ``has_user_provided_content_context``) for
the one-chat-pipeline plan (docs/architecture/chat-quality-history-and-plan.md
§7, slice 5b): a Strict turn may always read what the user attached or
pasted, even with zero knowledge-base evidence — Strict/Open governs KB
grounding, never whether the model may look at the user's own input.

Used by :func:`app.services.partner_chat.retrieve_context` (the Strict
zero-chunk refusal exception, internal surface only) and
:func:`app.services.partner_chat._judge_composed_answer` (skip the grounding
check/repair, which has no articles to check a screenshot description
against).
"""

from __future__ import annotations

import re

from app.services.chat_attachments import UPLOADED_PDF_CONTENT_MARKER

# Mirrors klai_kb_answer_policy.USER_ATTACHMENT_PART_TYPES.
USER_ATTACHMENT_PART_TYPES = frozenset({"file", "image", "image_url", "input_file", "input_image"})

# Mirrors klai_kb_answer_policy.USER_VISIBLE_CONVERSATION_QUERY_RE: an explicit
# question about the conversation itself is user-provided content too — the
# conversation is always available, unlike an attachment that may or may not
# be there. An attachment WORD alone ("screenshot", "bijlage") is deliberately
# NOT enough on its own (klai_kb_answer_policy.USER_ATTACHMENT_REFERENCE_QUERY_RE):
# without a match below or an actual attachment, this returns False either way.
USER_VISIBLE_CONVERSATION_QUERY_RE = re.compile(
    r"\b(wat\s+(zei|schreef)\s+ik|wat\s+staat\s+(hierboven|daarboven)|"
    r"(vorige|eerdere)\s+(bericht|vraag)|dit\s+gesprek|deze\s+"
    r"(chat|conversatie)|chatgeschiedenis|conversation\s+history)\b",
    re.IGNORECASE,
)


def _has_attachment(messages: list[dict]) -> bool:
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            if UPLOADED_PDF_CONTENT_MARKER in content:
                return True
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in USER_ATTACHMENT_PART_TYPES:
                return True
    return False


def has_user_provided_content(messages: list[dict], query: object) -> bool:
    """True when this turn can be answered from the user's own input.

    Ordinary chat text is not enough on its own: every latest user message
    counting as "user-provided content" would let Strict + zero results
    through as a general-knowledge answer. An attachment (an image/file part,
    or a converted PDF's marker text) always counts; an explicit question
    about the visible conversation does too, because the conversation is
    always available.
    """
    if _has_attachment(messages):
        return True
    return isinstance(query, str) and bool(USER_VISIBLE_CONVERSATION_QUERY_RE.search(query))
