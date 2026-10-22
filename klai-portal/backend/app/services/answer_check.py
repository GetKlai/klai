"""The check after the reply: which of the answer's statements a passage or the user carries.

One shape for both halves. :func:`check_hard_facts` reads the e-mail
addresses, links, phone numbers and amounts with regexes, synchronously, so
the footer can warn about the ones nothing carries;
``answer_grounding.check_statements`` asks a model for the answer's claims
after the reply went out. The footer, ``answer_signals.hard_facts_outside_kb``,
``.unsupported`` and ``.grounding`` all read an :class:`AnswerCheck`.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.services.citations import source_url_key, urls_in_passages

Kind = Literal["claim", "email", "link", "phone", "amount"]


@dataclass(frozen=True)
class Statement:
    """One thing the answer states, and the passage or user message that carries it (``None``: nothing does)."""

    text: str
    kind: Kind
    support: str | None


@dataclass(frozen=True)
class AnswerCheck:
    statements: list[Statement]

    @property
    def claims(self) -> list[Statement]:
        return [item for item in self.statements if item.kind == "claim"]

    @property
    def unsupported(self) -> list[Statement]:
        """The claims no passage carries, as the statement check judged them."""
        return [item for item in self.claims if item.support is None]

    @property
    def hard_facts_outside(self) -> dict[str, int]:
        """Per kind, how many e-mail addresses, links, phone numbers and amounts nothing carries."""
        return dict(Counter(item.kind for item in self.statements if item.kind != "claim" and item.support is None))


# A phone number: nine or more digits with optional spaces, dots, slashes,
# dashes or brackets, starting with a country code or a zero. "0031" or "085"
# alone is a number type in these articles and does not match; a date range
# ("01-09-2026 - 30-09-2026") is excluded by its year.
_PHONE_RE = re.compile(r"(?<![\w@.])(?:\+\d{1,3}|0)[\d\s()./\-]{7,}\d(?![\w@])")
_DATE_RE = re.compile(r"\d{2}-\d{2}-\d{4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# The last nine digits identify a number whether it is written with a country
# code, a zero or a bracketed zero.
_NUMBER_TAIL = 9


# An amount: a number beside a currency sign or word. Compared as currency
# plus value, so "€ 1.250,00" and "1250 euro" are the same amount and "€ 12,50"
# is not. A last separator followed by exactly three digits groups thousands
# ("1.250", "1,250,000"); any other count of digits after it is a fraction
# ("12,50", "16.95", "0,00576").
_AMOUNT_RE = re.compile(
    r"(?P<pre>[€$£]|\beur\b)\s?(?P<pre_number>\d(?:[\d.,]*\d)?)"
    r"|(?P<post_number>\d(?:[\d.,]*\d)?)\s?(?P<post>[€$£]|\beuro?\b)",
    re.IGNORECASE,
)
_AMOUNT_FRACTION_RE = re.compile(r"[.,](\d+)$")
# A markdown image: its target is a picture the writer was told to show, not a link it made up.
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_CURRENCIES = {"€": "EUR", "eur": "EUR", "euro": "EUR", "$": "USD", "£": "GBP"}


def _phone_numbers(text: str) -> set[str]:
    return {
        re.sub(r"\D", "", m)[-_NUMBER_TAIL:]
        for m in _PHONE_RE.findall(text)
        if len(re.sub(r"\D", "", m)) >= _NUMBER_TAIL and not _DATE_RE.search(m)
    }


def _emails(text: str) -> set[str]:
    return {m.lower() for m in _EMAIL_RE.findall(text)}


def _amounts(text: str) -> set[str]:
    return {
        _amount_key(match["pre"] or match["post"], match["pre_number"] or match["post_number"])
        for match in _AMOUNT_RE.finditer(text)
    }


def _amount_key(currency: str, number: str) -> str:
    fraction = _AMOUNT_FRACTION_RE.search(number)
    if fraction and len(fraction.group(1)) != 3:
        integer, decimals = number[: fraction.start()], fraction.group(1)
    else:
        integer, decimals = number, "0"
    value = Decimal(f"{re.sub(r'[^0-9]', '', integer) or '0'}.{decimals}").normalize()
    return f"{_CURRENCIES[currency.lower()]} {value:f}"


def _links(text: str) -> set[str]:
    return {key for url in urls_in_passages([{"text": text}]) if (key := _link_key(url))}


def _link_key(url: str) -> str | None:
    # A model can write a token that looks like a link but is not one
    # ("http://[stap 1]"): the normaliser raises on it, and a reply must not
    # fail on a malformed link it never cited (internal meetlat, 22 October).
    try:
        return source_url_key(url)
    except ValueError:
        return None


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


# Kind order is the order of the per-kind counts in the footer and the record.
_HARD_FACTS: dict[Kind, Callable[[str], set[str]]] = {
    "email": _emails,
    "link": _links,
    "phone": _phone_numbers,
    "amount": _amounts,
}


def check_hard_facts(text: str, chunks: list[dict] | None, messages: list[dict]) -> AnswerCheck:
    """Every e-mail address, link, phone number and amount in ``text``, with the passage or user text that holds it.

    The same provenance rule as ``chat_safety.without_unsourced_contact_details``,
    for a reply that is already on screen and can only be flagged: in the
    internal chat of 14 to 21 October an Open reply gave an e-mail address for
    a data-processing agreement and another a product price, neither in the
    knowledge base. A chunk's own source address counts as standing in it, and
    so does whatever the user wrote or pasted in any turn of ``messages``: a
    reply that repeats it did not invent it. A markdown image is not a link:
    the prompt hands the writer the passages' images to show. A fact is
    compared in one notation (the last nine digits of a number, an amount as
    currency plus value), and that notation is the statement's text.
    """
    carriers = [(str(chunk.get("text") or ""), source_url_key(chunk.get("source_url"))) for chunk in chunks or []]
    carriers += [(user_text, None) for user_text in _user_texts(messages)]
    statements = []
    for kind, facts_in in _HARD_FACTS.items():
        held = [
            (carrier, facts_in(carrier) | ({source} if kind == "link" and source else set()))
            for carrier, source in carriers
        ]
        stated = facts_in(_MARKDOWN_IMAGE_RE.sub("", text) if kind == "link" else text)
        statements += [
            Statement(fact, kind, next((carrier for carrier, facts in held if fact in facts), None))
            for fact in sorted(stated)
        ]
    return AnswerCheck(statements)
