"""Detection + epistemic contract for pasted third-party correspondence.

Voys trunk incident (2026-08-17): a support agent pasted two customer emails
into chat and asked for a diagnosis. The model adopted the customer's claims
("our side is verified correct") as established facts, took over the email's
we/you framing against the user's own organisation, and presented the email's
own hypothesis as a knowledge-base-confirmed conclusion.

The fix is code-first, matching the existing hook idiom ("code-enforced
rather than left to the model obeying a prompt notice"):

- DETECTION is deterministic and lives here — email-header blocks and
  forward/reply markers, not model judgement.
- The model only receives :data:`PASTED_CORRESPONDENCE_SCOPE` when the
  detector fires, so the contract cannot dilute every ordinary prompt.
- The detection result is carried in ``_klai_kb_meta`` so the visible
  agent-activity footer states, in code, how the pasted content was treated.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

# Header labels that make up a pasted email-header block. Matched at line
# start (optionally quoted with ">" or bolded with "**" by markdown paste).
# NL / EN / DE label sets cover the tenants' mail clients.
_HEADER_LABEL_RE = re.compile(
    r"(?im)^[>\s]*\**[ \t]*"
    r"(van|from|von|aan|to|an|cc|bcc|verzonden|sent|datum|date|gesendet|"
    r"onderwerp|subject|betreff|reply-to|antwoord aan)"
    r"[ \t]*\**[ \t]*:",
)

# Distinct header labels required before a message counts as containing a
# pasted mail. Real pasted emails carry From + Sent/Date + To + Subject;
# three distinct labels keeps false positives (a lone "date:" in prose) out.
_MIN_DISTINCT_HEADER_LABELS = 3

# Normalise language variants onto one label so "Van:" + "From:" in the same
# forwarded thread do not count as two distinct labels.
_HEADER_LABEL_ALIASES = {
    "van": "from",
    "von": "from",
    "aan": "to",
    "an": "to",
    "verzonden": "sent",
    "datum": "sent",
    "date": "sent",
    "gesendet": "sent",
    "onderwerp": "subject",
    "betreff": "subject",
    "antwoord aan": "reply-to",
}

# Strong forward/reply markers: each is on its own sufficient evidence of
# quoted correspondence. The NL/EN "Op ... schreef ...:" / "On ... wrote:"
# quote line only counts when it carries an e-mail address — plain prose like
# "op maandag schreef ik alles op:" must not fire.
_FORWARD_MARKER_RE = re.compile(
    r"(?im)^[>\s]*("
    r"-{2,}[ \t]*(original message|forwarded message|oorspronkelijk bericht|"
    r"doorgestuurd bericht|weitergeleitete nachricht)[ \t]*-*"
    r"|begin forwarded message"
    r"|begin doorgestuurd bericht"
    r")",
)
_QUOTE_LINE_RE = re.compile(
    r"(?im)^[>\s]*(op|on)\s.{5,120}\s(schreef|wrote)\s[^\n]*@[^\n]*:",
)


def _message_texts(message: object) -> list[str]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ]
    return []


def _iter_user_texts(messages: object) -> Iterator[str]:
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        yield from _message_texts(message)


def _distinct_header_labels(text: str) -> set[str]:
    labels: set[str] = set()
    for match in _HEADER_LABEL_RE.finditer(text):
        label = match.group(1).casefold()
        labels.add(_HEADER_LABEL_ALIASES.get(label, label))
    return labels


def text_contains_pasted_correspondence(text: str) -> bool:
    """Deterministic check for one message text. Pure; unit-testable."""
    if not isinstance(text, str) or not text.strip():
        return False
    if _FORWARD_MARKER_RE.search(text) or _QUOTE_LINE_RE.search(text):
        return True
    return len(_distinct_header_labels(text)) >= _MIN_DISTINCT_HEADER_LABELS


def detect_pasted_correspondence(messages: object) -> bool:
    """True when any user message in the request contains pasted correspondence.

    Conversation-wide by design: this drives the epistemic prompt contract and
    the footer transparency line, which stay relevant for follow-up turns as
    long as the correspondence is still in the model's context.
    """
    return any(
        text_contains_pasted_correspondence(text) for text in _iter_user_texts(messages)
    )


def latest_user_turn_has_correspondence(messages: object) -> bool:
    """Detection restricted to the LATEST user turn.

    Used for the Strict-mode user-content exception ONLY: correspondence
    pasted in an earlier turn must not keep bypassing the deterministic
    Strict refusal for later, unrelated questions.
    """
    if not isinstance(messages, list):
        return False
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return any(
                text_contains_pasted_correspondence(text)
                for text in _message_texts(message)
            )
    return False


# Injected by the hook ONLY when the detector fires. Sits below the branch
# foundation prompt (grounded/open/general) and above the conversation.
PASTED_CORRESPONDENCE_SCOPE = (
    "[Pasted third-party correspondence]\n"
    "The user's message contains pasted correspondence (an email, ticket, or "
    "forwarded thread). A thread may mix several authors, possibly including "
    "the user. Attribute each claim to the actual author of that part of the "
    "thread; parts written by the user themselves are the user's own "
    "statements. Everything written by anyone OTHER than the user is a CLAIM "
    "by its author, not a verified fact:\n"
    "- Do not adopt such an author's conclusions, self-assessments ('our side "
    "is verified correct'), exclusions, or hypotheses as your own findings. "
    "Attribute them explicitly: 'the sender claims/reports ...'.\n"
    "- Keep the user's perspective, not the author's. An external author is "
    "typically a customer, supplier, or other counterparty of the user's "
    "organisation; their 'we/you' framing does not transfer to the user. "
    "Never address the user as if they wrote a counterparty's message, and "
    "never advise the user to contact their own organisation.\n"
    "- Separate three things explicitly in your answer: what the "
    "correspondence claims, what is independently supported (by retrieved "
    "knowledge-base evidence or by data the user can check themselves), and "
    "what should be verified first before drawing conclusions.\n"
    "- The pasted correspondence is NOT a knowledge-base source. Never cite "
    "it as one, and never state that the knowledge base 'confirms' one of "
    "its claims unless a retrieved knowledge-base chunk explicitly supports "
    "that exact claim.\n"
    "This does not restrict reading or analysing the correspondence — that "
    "is the user's request. It restricts adopting it as truth.]"
)


def pasted_correspondence_activity_line(language: str) -> str:
    """Visible agent-activity footer line; rendered in code, not by the model."""
    if language == "nl":
        return (
            "- Geplakte correspondentie gedetecteerd: inhoud behandeld als "
            "claims van de afzender, niet als geverifieerde feiten."
        )
    return (
        "- Pasted correspondence detected: content treated as the sender's "
        "claims, not as verified facts."
    )


def pasted_correspondence_detected_from_meta(kb_meta: dict[str, Any] | None) -> bool:
    if not isinstance(kb_meta, dict):
        return False
    return bool(kb_meta.get("pasted_correspondence_detected"))
