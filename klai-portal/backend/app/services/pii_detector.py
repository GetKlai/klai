"""Regex-based PII detector used by the guardrails layer.

Detectors are kept deliberately simple — regex-level heuristics that catch
the common Dutch / EU PII shapes. The LiteLLM hook calls portal-api to
resolve which detectors apply for a user (based on their active rules)
and then runs :func:`scan` / :func:`redact` on the user input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Dutch + common patterns
_EMAIL = re.compile(r"\b[\w.-]+@[\w.-]+\.\w{2,}\b")
_BSN = re.compile(r"\b\d{9}\b")  # 9 digits; 11-proof check optional
_PHONE_NL = re.compile(r"(?:\+31|0)[1-9][\s-]?\d{1,3}[\s-]?\d{4,7}")
_CREDITCARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9]{4}[ ]?){2,7}[A-Z0-9]{1,4}\b")

_DETECTORS: dict[str, tuple[re.Pattern[str], str]] = {
    "email": (_EMAIL, "[EMAIL]"),
    "bsn": (_BSN, "[BSN]"),
    "phone": (_PHONE_NL, "[PHONE]"),
    "creditcard": (_CREDITCARD, "[CREDITCARD]"),
    "iban": (_IBAN, "[IBAN]"),
}


def detectors_for_rule(slug: str, rule_text: str) -> list[str]:
    """Return detector keys (e.g. ['email']) activated by this rule.

    Simple heuristic: a detector key is activated when its name appears in
    the rule slug (case-insensitive). ``rule_text`` is accepted for future
    expansion (e.g. per-rule overrides) but is currently unused.
    """
    del rule_text  # Currently unused; reserved for future per-rule overrides.
    s = slug.lower()
    return [key for key in _DETECTORS if key in s]


@dataclass
class PIIHit:
    detector: str
    matched: str


def scan(text: str, detectors: list[str]) -> list[PIIHit]:
    """Return all PII hits for the requested detector keys."""
    hits: list[PIIHit] = []
    for key in detectors:
        entry = _DETECTORS.get(key)
        if entry is None:
            continue
        pattern, _ = entry
        for m in pattern.finditer(text):
            hits.append(PIIHit(detector=key, matched=m.group(0)))
    return hits


def redact(text: str, detectors: list[str]) -> str:
    """Return text with each activated PII pattern replaced by its placeholder."""
    result = text
    for key in detectors:
        entry = _DETECTORS.get(key)
        if entry is None:
            continue
        pattern, placeholder = entry
        result = pattern.sub(placeholder, result)
    return result
