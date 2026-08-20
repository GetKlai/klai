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
_INLINE_HEADER_LABEL_RE = re.compile(
    r"(?i)\b(?:van|from|von|aan|to|an|cc|bcc|verzonden|sent|datum|date|"
    r"gesendet|onderwerp|subject|betreff|reply-to|antwoord aan)[ \t]*\**[ \t]*:",
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
_INLINE_FORWARD_MARKER_RE = re.compile(
    r"(?i)("
    r"-{2,}[ \t]*(original message|forwarded message|oorspronkelijk bericht|"
    r"doorgestuurd bericht|weitergeleitete nachricht)[ \t]*-*"
    r"|begin forwarded message"
    r"|begin doorgestuurd bericht"
    r")",
)
_QUOTE_LINE_RE = re.compile(
    r"(?im)^[>\s]*(op|on)\s.{5,120}\s(schreef|wrote)\s[^\n]*@[^\n]*:",
)
_INLINE_QUOTE_LINE_RE = re.compile(
    r"(?i)\b(op|on)\s.{5,120}\s(schreef|wrote)\s[^\n]*@[^\n]*:",
)

ANSWER_CONTRACT_MARKERS = (
    "[[KLAI_CORRESPONDENCE_SENDER_STATEMENTS]]",
    "[[KLAI_CORRESPONDENCE_KB_EVIDENCE]]",
    "[[KLAI_CORRESPONDENCE_OPEN_QUESTIONS]]",
    "[[KLAI_CORRESPONDENCE_VERIFY_FIRST]]",
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


def extract_pasted_correspondence_text(
    text: str, *, assume_detected: bool = False
) -> str:
    """Return the detected correspondence portion, excluding leading user prose."""
    if not assume_detected and not text_contains_pasted_correspondence(text):
        return ""

    starts = [
        match.start()
        for pattern in (_FORWARD_MARKER_RE, _QUOTE_LINE_RE)
        if (match := pattern.search(text)) is not None
    ]
    header_matches = list(_HEADER_LABEL_RE.finditer(text))
    if len(_distinct_header_labels(text)) >= _MIN_DISTINCT_HEADER_LABELS:
        starts.append(header_matches[0].start())
    if assume_detected and not starts:
        for pattern in (
            _INLINE_FORWARD_MARKER_RE,
            _INLINE_QUOTE_LINE_RE,
            _INLINE_HEADER_LABEL_RE,
        ):
            inline_match = pattern.search(text)
            if inline_match is not None:
                starts.append(inline_match.start())
    return text[min(starts) :].strip() if starts else ""


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
# foundation prompt (grounded/open/general) and above the conversation. The
# machine markers are stripped from both response modes before display.
PASTED_CORRESPONDENCE_SCOPE = (
    "[Pasted third-party correspondence]\n"
    "The user's message contains correspondence from one or more authors. "
    "Treat it as context about what each sender states. Produce the complete "
    "answer as exactly four sections in the order below. The first character "
    "of the response must be the first marker. Start every section "
    "with its marker verbatim, followed by a short heading in the user's "
    "language and the section content.\n"
    f"{ANSWER_CONTRACT_MARKERS[0]}\n"
    "State what the sender says. Attribute every statement to its actual author, "
    "including self-assessments, exclusions, and hypotheses.\n"
    f"{ANSWER_CONTRACT_MARKERS[1]}\n"
    "State what the retrieved knowledge-base evidence says about the situation. "
    "Attach the matching internal evidence label shown in the context to every "
    "supported statement, using exactly `(E<n>)` after the statement (for example "
    "`(E1)`); do not use `E1:` or `[E1]`. These internal `(E<n>)` labels are the "
    "sole exception to "
    "the general no-citation-marker instruction. When no retrieved evidence "
    "addresses the situation, "
    "say so in this section.\n"
    f"{ANSWER_CONTRACT_MARKERS[2]}\n"
    "State what remains open after comparing the correspondence with the "
    "retrieved evidence.\n"
    f"{ANSWER_CONTRACT_MARKERS[3]}\n"
    "Give concrete checks the reader can perform first.\n"
    "Use these four sections once each and return the markers exactly as written.]"
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
