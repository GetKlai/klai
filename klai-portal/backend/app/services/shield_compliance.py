"""Deterministic Shield compliance checks.

This is intentionally rule-based for the first platform-admin test slice:
the browser extension, API, and MCP can all call the same pure function
without introducing a model dependency before policy behaviour is stable.
"""

from __future__ import annotations

import re
from typing import Literal, TypedDict

ShieldLevel = Literal["basic", "extended", "strict"]
ShieldCheckType = Literal["input", "output"]
ShieldStatus = Literal["green", "yellow", "orange", "red"]


class ShieldWarning(TypedDict):
    id: str
    label: str
    severity: ShieldStatus
    category: str
    snippet: str | None


class ShieldResult(TypedDict):
    status: ShieldStatus
    risk_score: int
    should_block: bool
    should_warn: bool
    level: ShieldLevel
    check_type: ShieldCheckType
    warnings: list[ShieldWarning]


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_NL_PHONE_RE = re.compile(r"(?<!\d)(?:\+31|0031|0)\s?(?:6|[1-9]\d)\s?(?:[\s.-]?\d){7,8}(?!\d)")
_NL_POSTCODE_RE = re.compile(r"\b[1-9][0-9]{3}\s?[A-Z]{2}\b", re.IGNORECASE)
_CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7,16}\b", re.IGNORECASE)
_BSN_CANDIDATE_RE = re.compile(r"(?<!\d)\d{8,9}(?!\d)")

_POLICY_PATTERNS: list[tuple[str, str, ShieldStatus, str, set[ShieldLevel]]] = [
    (
        "ai-act-prohibited-social-scoring",
        "Mogelijke verboden AI-praktijk: social scoring.",
        "red",
        r"\b(social scoring|sociaal krediet|sociale score|citizen score)\b",
        {"strict"},
    ),
    (
        "ai-act-prohibited-manipulation",
        "Mogelijke verboden AI-praktijk: manipulatie of dark pattern.",
        "red",
        r"\b(manipuleer|manipulate|subliminal|dark pattern|misleid)\b",
        {"strict"},
    ),
    (
        "ai-act-biometrics",
        "Biometrie vraagt om extra juridische controle.",
        "orange",
        r"\b(biometric|biometrie|face recognition|gezichtsherkenning|iris|vingerafdruk)\b",
        {"extended", "strict"},
    ),
    (
        "ai-act-high-risk",
        "Mogelijk high-risk domein volgens de AI Act.",
        "orange",
        r"\b(kredietwaardigheid|credit score|sollicitatie|hiring|ontslag|verzekering|medical device|diagnose)\b",
        {"extended", "strict"},
    ),
    (
        "ai-act-emotion-recognition",
        "Emotieherkenning is gevoelig en mogelijk beperkt toegestaan.",
        "orange",
        r"\b(emotion recognition|emotieherkenning|sentiment van medewerkers|detecteer emoties)\b",
        {"extended", "strict"},
    ),
    (
        "ai-act-deepfake",
        "Synthetische of deepfake-content vraagt om transparantie.",
        "yellow",
        r"\b(deepfake|synthetische stem|synthetic voice|voice clone|stemklonen)\b",
        {"basic", "extended", "strict"},
    ),
    (
        "ai-act-transparency",
        "Zorg dat duidelijk is dat de gebruiker met AI communiceert.",
        "yellow",
        r"\b(chatbot|ai assistant|virtuele assistent|automated decision|geautomatiseerd besluit)\b",
        {"basic", "extended", "strict"},
    ),
]


def _valid_bsn(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) == 8:
        digits = "0" + digits
    if len(digits) != 9:
        return False
    checksum = sum((9 - index) * int(digit) for index, digit in enumerate(digits[:8]))
    checksum -= int(digits[-1])
    return checksum % 11 == 0


def _mask_snippet(text: str, start: int, end: int) -> str:
    snippet = text[max(0, start - 24) : min(len(text), end + 24)]
    return snippet.strip()


def _add_regex_warning(
    warnings: list[ShieldWarning],
    *,
    text: str,
    pattern: re.Pattern[str],
    warning_id: str,
    label: str,
    severity: ShieldStatus,
    category: str,
    validator: object | None = None,
) -> None:
    for match in pattern.finditer(text):
        matched = match.group(0)
        if validator is not None and not validator(matched):  # type: ignore[operator]
            continue
        warnings.append(
            {
                "id": warning_id,
                "label": label,
                "severity": severity,
                "category": category,
                "snippet": _mask_snippet(text, match.start(), match.end()),
            }
        )


def _normalise_level(level: str | None) -> ShieldLevel:
    if level in {"basic", "extended", "strict"}:
        return level  # type: ignore[return-value]
    return "basic"


def _normalise_check_type(check_type: str | None) -> ShieldCheckType:
    if check_type in {"input", "output"}:
        return check_type  # type: ignore[return-value]
    return "input"


def _score_for_status(status: ShieldStatus) -> int:
    return {"green": 0, "yellow": 35, "orange": 65, "red": 95}[status]


def _max_status(warnings: list[ShieldWarning]) -> ShieldStatus:
    if any(w["severity"] == "red" for w in warnings):
        return "red"
    if any(w["severity"] == "orange" for w in warnings):
        return "orange"
    if any(w["severity"] == "yellow" for w in warnings):
        return "yellow"
    return "green"


def check_compliance(
    text: str,
    *,
    level: str | None = "basic",
    check_type: str | None = "input",
) -> ShieldResult:
    """Check a prompt or generated answer for policy and privacy risk."""
    level_value = _normalise_level(level)
    check_type_value = _normalise_check_type(check_type)
    warnings: list[ShieldWarning] = []

    _add_regex_warning(
        warnings,
        text=text,
        pattern=_EMAIL_RE,
        warning_id="privacy-email",
        label="E-mailadres gevonden.",
        severity="red",
        category="privacy",
    )
    _add_regex_warning(
        warnings,
        text=text,
        pattern=_NL_PHONE_RE,
        warning_id="privacy-phone",
        label="Telefoonnummer gevonden.",
        severity="red",
        category="privacy",
    )
    _add_regex_warning(
        warnings,
        text=text,
        pattern=_NL_POSTCODE_RE,
        warning_id="privacy-postcode",
        label="Postcode gevonden.",
        severity="yellow",
        category="privacy",
    )
    _add_regex_warning(
        warnings,
        text=text,
        pattern=_CREDIT_CARD_RE,
        warning_id="privacy-payment-card",
        label="Mogelijk betaalkaartnummer gevonden.",
        severity="red",
        category="privacy",
    )
    _add_regex_warning(
        warnings,
        text=text,
        pattern=_IBAN_RE,
        warning_id="privacy-iban",
        label="IBAN gevonden.",
        severity="red",
        category="privacy",
    )
    _add_regex_warning(
        warnings,
        text=text,
        pattern=_BSN_CANDIDATE_RE,
        warning_id="privacy-bsn",
        label="Mogelijk BSN gevonden.",
        severity="red",
        category="privacy",
        validator=_valid_bsn,
    )

    for warning_id, label, severity, pattern, levels in _POLICY_PATTERNS:
        if level_value not in levels:
            continue
        compiled = re.compile(pattern, re.IGNORECASE)
        _add_regex_warning(
            warnings,
            text=text,
            pattern=compiled,
            warning_id=warning_id,
            label=label,
            severity=severity,
            category="ai_act",
        )

    status = _max_status(warnings)
    return {
        "status": status,
        "risk_score": _score_for_status(status),
        "should_block": status == "red",
        "should_warn": status in {"yellow", "orange"},
        "level": level_value,
        "check_type": check_type_value,
        "warnings": warnings[:25],
    }
