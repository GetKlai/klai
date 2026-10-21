"""Safety refusals, fixed replies and the provenance checks for contact details and other hard facts of the chat path.

Also the appointment escalation signal, which decides from the answer text in code.
"""

from __future__ import annotations

import re
from collections.abc import AsyncGenerator
from typing import Any

from klai_chat_prompts.language import (
    identify_text_language,
)

from app.services.chat_sse import _sse_content_delta
from app.services.citations import source_url_key, urls_in_passages
from app.services.llm_safety_adapter import (
    check_context_text,
    check_model_output,
    check_widget_or_partner_input,
    safe_refusal_text,
)


def _last_user_message(messages: list[dict]) -> str | None:
    """Extract the last user message from the messages array."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if p.get("type") == "text")
    return None


def safety_refusal_message(visitor_text: str = "") -> str:
    """Safety refusal in the visitor's own language.

    Takes the visitor's own last message and runs it through the one shared
    identifier (``klai_chat_prompts.language``, same mechanism as the canned
    KB refusals; abstention falls back to Dutch). NEVER pass a rewritten
    retrieval query here: the refusal language belongs to the visitor, not
    to retrieval.
    """
    return safe_refusal_text(identify_text_language(visitor_text))


def widget_input_safety_violation(messages: list[dict]) -> str | None:
    """Return a safety reason for widget input that must not reach retrieval or the LLM."""
    decision = check_widget_or_partner_input(messages)
    return None if decision.allowed else decision.reason


def output_safety_violation(text: str) -> str | None:
    """Return a safety reason for generated content that must not be shown."""
    decision = check_model_output(text)
    return None if decision.allowed else decision.reason


def context_safety_violation(text: str, *, query: str = "") -> str | None:
    """Return a safety reason for untrusted context that must not enter prompts."""
    decision = check_context_text(text, query=query)
    return None if decision.allowed else decision.reason


def safety_refusal_response(*, model: str, query: str = "") -> dict:
    return {
        "id": "chatcmpl-safety-refusal",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": safety_refusal_message(query), "sources": []},
                "finish_reason": "content_filter",
            }
        ],
    }


# A phone number: nine or more digits with optional spaces, dots, slashes,
# dashes or brackets, starting with a country code or a zero. "0031" or "085"
# alone is a number type in these articles and does not match; a date range
# ("01-09-2026 - 30-09-2026") is excluded by its year.
_PHONE_RE = re.compile(r"(?<![\w@.])(?:\+\d{1,3}|0)[\d\s()./\-]{7,}\d(?![\w@])")
_DATE_RE = re.compile(r"\d{2}-\d{2}-\d{4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_ORPHAN_MARKER_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*$")
# The last nine digits identify a number whether it is written with a country
# code, a zero or a bracketed zero.
_NUMBER_TAIL = 9


# An amount: a number beside a currency sign or word. Compared without its
# separators and without a trailing ",00" or ",-", so "€ 1.250" and "1250 euro"
# are the same amount.
_AMOUNT_RE = re.compile(
    r"(?:[€$£]|\beur\b)\s?(\d(?:[\d.,]*\d)?)|(\d(?:[\d.,]*\d)?)\s?(?:[€$£]|\beuro?\b)", re.IGNORECASE
)
_AMOUNT_ZERO_CENTS_RE = re.compile(r"[.,](?:00|-)$")


def _phone_numbers(text: str) -> set[str]:
    return {
        re.sub(r"\D", "", m)[-_NUMBER_TAIL:]
        for m in _PHONE_RE.findall(text)
        if len(re.sub(r"\D", "", m)) >= _NUMBER_TAIL and not _DATE_RE.search(m)
    }


def _emails(text: str) -> set[str]:
    return {m.lower() for m in _EMAIL_RE.findall(text)}


def _contact_details(text: str) -> set[str]:
    return _phone_numbers(text) | _emails(text)


def _amounts(text: str) -> set[str]:
    return {
        re.sub(r"\D", "", _AMOUNT_ZERO_CENTS_RE.sub("", match.group(1) or match.group(2)))
        for match in _AMOUNT_RE.finditer(text)
    }


def _links(texts: list[str]) -> set[str]:
    return {key for url in urls_in_passages([{"text": text} for text in texts]) if (key := source_url_key(url))}


def _user_texts(messages: list[dict]) -> list[str]:
    texts = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(part["text"] for part in content if part.get("type") == "text" and "text" in part)
    return texts


def hard_facts_outside(text: str, chunks: list[dict] | None, messages: list[dict]) -> dict[str, int]:
    """Per kind, how many e-mail addresses, links, phone numbers and amounts in ``text`` stand in no chunk.

    The same provenance rule as :func:`without_unsourced_contact_details`, for
    a reply that is already on screen and can only be flagged: in the internal
    chat of 14 to 21 October an Open reply gave an e-mail address for a
    data-processing agreement and another a product price, neither in the
    knowledge base. A chunk's own source address counts as standing in it, and
    so does whatever the user wrote or pasted in any turn of ``messages``: a
    reply that repeats it did not invent it.
    """
    passages = [str(chunk.get("text") or "") for chunk in chunks or []] + _user_texts(messages)
    known = "\n".join(passages)
    sources = {key for chunk in chunks or [] if (key := source_url_key(chunk.get("source_url")))}
    outside = {
        "email": _emails(text) - _emails(known),
        "link": _links([text]) - _links(passages) - sources,
        "phone": _phone_numbers(text) - _phone_numbers(known),
        "amount": _amounts(text) - _amounts(known),
    }
    return {kind: len(values) for kind, values in outside.items() if values}


def without_unsourced_contact_details(text: str, chunks: list[dict] | None) -> str:
    """Drop every sentence with a phone number or e-mail address that does not stand in ``chunks``.

    The profile forbids repeating a support number from an article and the
    model does it anyway; the check flags the sentence and nothing acts. In
    the first week of real traffic after the selection step the widget gave a
    Belgian number from an article about another subject three times and an
    invented head-office number once. Code decides on provenance: a number or
    address that stands in the passages the writer read may stay.
    """
    known: set[str] = set()
    for chunk in chunks or []:
        if isinstance(chunk.get("text"), str):
            known |= _contact_details(chunk["text"])
    kept_lines: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            kept_lines.append(line)
            continue
        sentences = [s for s in _SENTENCE_RE.split(line) if (_contact_details(s) - known) == set()]
        if not sentences or all(_ORPHAN_MARKER_RE.match(s) for s in sentences):
            continue
        kept_lines.append(" ".join(sentences))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()


async def safety_refusal_stream(query: str = "") -> AsyncGenerator[bytes]:
    yield _sse_content_delta(safety_refusal_message(query))
    yield b"data: [DONE]\n\n"


def fixed_reply_response(*, model: str, message: str) -> dict:
    """A reply decided without a model: an unreadable PDF attachment, or a
    Strict turn that cannot search the knowledge base.

    ``message`` is already rendered in the conversation's language; no
    generation happens for this turn, matching the LiteLLM hook's
    ``mock_response`` short-circuit.
    """
    return {
        "id": "chatcmpl-fixed-reply",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": message, "sources": []},
                "finish_reason": "stop",
            }
        ],
    }


async def fixed_reply_stream(message: str) -> AsyncGenerator[bytes]:
    yield _sse_content_delta(message)
    yield b"data: [DONE]\n\n"


_OFFER_NOUN_RE = re.compile(r"\b(afspraak|appointment)\b", re.IGNORECASE)


def _text_offers_appointment(text: str) -> bool:
    """Whether the visible reply actually offers an appointment.

    The marker is a machine signal the model is asked to add to such a reply;
    it is not evidence on its own. A reply that mentions the appointment
    counts; a list of steps with a stray token does not.
    """
    return bool(_OFFER_NOUN_RE.search(text))


def _appointment_escalation() -> dict[str, bool]:
    """The only escalation shape the widget contract allows.

    Exactly ``{"appointment": bool}``, never partial — the widget treats an
    absent key as "no offer", so a half-built dict would be a silent no-op.
    Built fresh per call so a caller mutating one decision cannot reach into
    another.
    """
    return {"appointment": True}


def _helpdesk_refusal_offers(citation_chunks: list[dict] | None) -> dict[str, Any]:
    """What the widget shows under a refusal: the appointment, and a broader search where one can run.

    A broad answer only runs on a turn where retrieval found nothing
    (_broad_mode_active). Offered over articles that were found, the visitor
    agrees and gets a second "not found".
    """
    offers: dict[str, Any] = {"escalation": _appointment_escalation()}
    if not citation_chunks:
        offers["broad_mode"] = "offer"
    return offers


def _appointment_escalation_signal(decision: object) -> dict[str, bool] | None:
    """Read the appointment offer back off a composed decision, or ``None``.

    Deliberately strict: anything other than a literal ``True`` under
    ``appointment`` is no offer. A safety refusal replaces the decision dict
    wholesale, so a blocked answer can never carry a booking button.
    """
    if not isinstance(decision, dict):
        return None
    escalation = decision.get("escalation")
    if isinstance(escalation, dict) and escalation.get("appointment") is True:
        return {"appointment": True}
    return None
