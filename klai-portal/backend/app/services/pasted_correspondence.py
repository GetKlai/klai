"""Detection + epistemic contract for pasted third-party correspondence.

Moved from ``deploy/litellm/klai_pasted_correspondence.py`` (one-chat-pipeline
plan, slice 3): the plan (docs/architecture/chat-quality-history-and-plan.md
§7.2) treats this as "really different" and keeps it a step that only fires
on detection, but wants exactly one implementation shared by every chat
surface — so it moves out of the LiteLLM-only hook into portal.

Voys trunk incident (2026-08-17): a support agent pasted two customer emails
into chat and asked for a diagnosis. The model adopted the customer's claims
("our side is verified correct") as established facts, took over the email's
we/you framing against the user's own organisation, and presented the email's
own hypothesis as a knowledge-base-confirmed conclusion.

The fix is code-first, matching the hook's original idiom
("code-enforced rather than left to the model obeying a prompt notice"):

- DETECTION is deterministic and lives here — email-header blocks and
  forward/reply markers, not model judgement.
- The model only receives :data:`PASTED_CORRESPONDENCE_SCOPE` when the
  detector fires, so the contract cannot dilute every ordinary prompt.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

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
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
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


def extract_pasted_correspondence_text(text: str, *, assume_detected: bool = False) -> str:
    """Return the detected correspondence portion, excluding leading user prose."""
    if not assume_detected and not text_contains_pasted_correspondence(text):
        return ""

    starts = [
        match.start() for pattern in (_FORWARD_MARKER_RE, _QUOTE_LINE_RE) if (match := pattern.search(text)) is not None
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

    Conversation-wide by design: this drives the epistemic prompt contract,
    which stays relevant for follow-up turns as long as the correspondence
    is still in the model's context (a short "waarom?" after a pasted email
    must still get it).
    """
    return any(text_contains_pasted_correspondence(text) for text in _iter_user_texts(messages))


def latest_user_turn_has_correspondence(messages: object) -> bool:
    """Detection restricted to the LATEST user turn.

    Used for the Strict-mode user-content exception ONLY (slice 4/5):
    correspondence pasted in an earlier turn must not keep bypassing the
    deterministic Strict refusal for later, unrelated questions.
    """
    if not isinstance(messages, list):
        return False
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return any(text_contains_pasted_correspondence(text) for text in _message_texts(message))
    return False


# Injected into the system prompt ONLY when the detector fires. Sits below
# the branch foundation prompt (grounded/support/broad) and above the
# retrieved-context block. The machine markers are stripped from the
# response before display (see answer_epistemics.strip_answer_contract_markers).
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


# The internal chat's material turn (SPEC-CHAT-INTERNAL-ROUTE-001 §5): the
# employee pasted something to work on, a customer's mail, a ticket, a log or
# a trace. Decided by these rules and never by a model: as a separate call the
# small model read "other" right 1 of 7 times, and as a field of the case it
# read pasted material as an ordinary question in 16 of 19 cases. On 76 real
# messages the word, mail and trace rules never marked an ordinary question
# and found 13 of 20 pasted texts; six of the seven misses were short mails
# without headers (the letter rule) and follow-ups such as "write the mail"
# (an earlier material message).
_MATERIAL_MIN_WORDS = 120
_TRACE_LINE_RE = re.compile(
    r"(?im)^[ \t>]*(?:"
    r"\[?\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}"
    r"|\[?\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}[ ,T]+\d{1,2}:\d{2}"
    r"|\[?\d{1,2}:\d{2}:\d{2}"
    r"|(?:INVITE|ACK|BYE|CANCEL|REGISTER|OPTIONS|PRACK|SUBSCRIBE|NOTIFY|PUBLISH|INFO|REFER|MESSAGE|UPDATE)\s+sips?:"
    r"|SIP/2\.0\s+\d{3}"
    r")",
)
# One timestamp in a question is a date, three lines starting with one are a log.
_MIN_TRACE_LINES = 3
# A salutation or a sign-off on its own line.
_LETTER_LINE_RE = re.compile(
    r"(?im)^[ \t>]*(?:"
    r"(?:beste|hallo|hello|hi|dear|dag|geachte|goedemorgen|goedemiddag|sehr geehrte[rs]?|liebe[rs]?|guten tag|bonjour)"
    r"[ \t]+[^\n]{1,40}?[,!]?[ \t]*$"
    r"|(?:met )?(?:vriendelijke|hartelijke) groet(?:en)?\b|mvg\b|mit freundlichen gr|freundliche gr|viele gr"
    r"|kind regards|best regards|regards,|thanks,|thank you,|groeten,|alvast bedankt|cordialement"
    r")",
)
# "Hallo, hoe stel ik X in?" is a question with a greeting; a letter has a few
# lines and at least 30 words around it.
_LETTER_MIN_LINES = 3
_LETTER_MIN_WORDS = 30
# The pasted text the Strict statement check reads as one more article: the
# latest characters, so the checker's prompt stays bounded on a long log.
_MATERIAL_ARTICLE_CHARS = 20_000


def _material_rules(text: str) -> list[str]:
    words = len(text.split())
    rules = []
    if words > _MATERIAL_MIN_WORDS:
        rules.append("words")
    if text_contains_pasted_correspondence(text):
        rules.append("mail")
    if len(_TRACE_LINE_RE.findall(text)) >= _MIN_TRACE_LINES:
        rules.append("trace")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) >= _LETTER_MIN_LINES and words >= _LETTER_MIN_WORDS and _LETTER_LINE_RE.search(text):
        rules.append("letter")
    return rules


def _user_texts(messages: list[dict]) -> list[str]:
    return ["\n".join(_message_texts(m)) for m in messages if m.get("role") == "user"]


def material_rules(messages: list[dict]) -> list[str]:
    """The rules the employee's latest message meets, else ``["earlier"]`` when an earlier one met any.

    A conversation that began with pasted material stays a material turn: the
    follow-up asks for something done with it. Every turn the mail detector
    fires on is one, because that detector is the ``mail`` rule.
    """
    texts = _user_texts(messages)
    if not texts:
        return []
    if latest := _material_rules(texts[-1]):
        return latest
    return ["earlier"] if any(_material_rules(text) for text in texts[:-1]) else []


def material_article(messages: list[dict]) -> tuple[str, str]:
    """The employee's own messages as the one extra article the Strict statement check reads."""
    text = "\n\n".join(text for text in _user_texts(messages) if text.strip())
    return ("Text the employee pasted into the conversation", text[-_MATERIAL_ARTICLE_CHARS:])


# The writer's brief on a material turn, instead of PASTED_CORRESPONDENCE_SCOPE.
# Replayed on 20 real internal turns with pasted text, two rounds each, a
# draft came where one was asked in 2 and 2 of 12 without it and in 8 and 9
# of 12 with it; a sender's claim stated as fact stayed at 1 and 1.
MATERIAL_BRIEF = (
    "[Pasted material]\n"
    "The employee pasted material into this conversation: a customer's mail, a ticket, a log or a trace.\n"
    "1. Everything in the pasted material is its author's statement, not an established fact. "
    "'We', 'you', 'our side' and 'us' inside it are its author's words, not the employee's or the employee's "
    "organisation's. Never present a sender's own assessment, diagnosis or exclusion as confirmed: attribute it "
    "('the customer says ...').\n"
    "2. Do what the employee asked in their own words around the material:\n"
    "- Asked for a reply, asked to answer the pasted mail or ticket, or a customer mail pasted with no instruction "
    "at all: an employee who pastes a customer's message and asks you to answer it means a reply to that customer, "
    "not an explanation to the employee. Write the reply the employee can send: it opens with a greeting to the "
    "customer, is in the customer's language and is ready to send. Statements about the product, "
    "prices, settings or procedures in it come only from the knowledge-base passages; what they do not cover you "
    "leave out or mark as [to check]. Under the reply, after a line '---', add a short note to the employee in the "
    "employee's language: what the passages did not cover and what to verify first.\n"
    "- Asked to analyse a trace or log, or to find something in it: answer from the pasted lines first and quote "
    "the lines you rely on; use the passages only to explain what those lines mean.\n"
    "- Asked to translate, summarise or review a text: do exactly that and nothing more.\n"
    "- Any other request: do that, with the material as the case and the passages for statements about the product.\n"
    "3. Do not answer in a fixed analysis format (what the sender says / what the knowledge base says / open "
    "questions). Do not refuse because the knowledge base has no matching article: the task on the material can "
    "always be done; only statements about the product need a passage.]"
)
# On a turn that searched, the brief closes the prompt after the passages and
# the passage brief, so the employee's task decides the form of the reply.
MATERIAL_BRIEF_AFTER_PASSAGES = (
    "\n\nThe knowledge-base lines above are the facts for statements about the product. "
    "What you write with them follows the employee's task:\n" + MATERIAL_BRIEF
)
