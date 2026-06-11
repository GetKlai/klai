"""Small deterministic Shield guardrail for MCP tools."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_NL_PHONE_RE = re.compile(r"(?<!\d)(?:\+31|0031|0)\s?(?:6|[1-9]\d)\s?(?:[\s.-]?\d){7,8}(?!\d)")
_CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7,16}\b", re.IGNORECASE)
_BSN_CANDIDATE_RE = re.compile(r"(?<!\d)\d{8,9}(?!\d)")

_POLICY_PATTERNS: list[tuple[str, str, str, str, set[str]]] = [
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
        r"\b(kredietwaardigheid|credit score|sollicitatie|hiring|ontslag|verzekering|diagnose)\b",
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


def _snippet(text: str, start: int, end: int) -> str:
    return text[max(0, start - 24) : min(len(text), end + 24)].strip()


def _add_match(
    warnings: list[dict],
    *,
    text: str,
    pattern: re.Pattern[str],
    warning_id: str,
    label: str,
    severity: str,
    category: str,
    validator=None,
) -> None:
    for match in pattern.finditer(text):
        if validator is not None and not validator(match.group(0)):
            continue
        warnings.append(
            {
                "id": warning_id,
                "label": label,
                "severity": severity,
                "category": category,
                "snippet": _snippet(text, match.start(), match.end()),
            }
        )


def check_compliance(text: str, *, level: str = "basic", check_type: str = "input") -> dict:
    level = level if level in {"basic", "extended", "strict"} else "basic"
    check_type = check_type if check_type in {"input", "output"} else "input"
    warnings: list[dict] = []

    _add_match(
        warnings,
        text=text,
        pattern=_EMAIL_RE,
        warning_id="privacy-email",
        label="E-mailadres gevonden.",
        severity="red",
        category="privacy",
    )
    _add_match(
        warnings,
        text=text,
        pattern=_NL_PHONE_RE,
        warning_id="privacy-phone",
        label="Telefoonnummer gevonden.",
        severity="red",
        category="privacy",
    )
    _add_match(
        warnings,
        text=text,
        pattern=_CREDIT_CARD_RE,
        warning_id="privacy-payment-card",
        label="Mogelijk betaalkaartnummer gevonden.",
        severity="red",
        category="privacy",
    )
    _add_match(
        warnings,
        text=text,
        pattern=_IBAN_RE,
        warning_id="privacy-iban",
        label="IBAN gevonden.",
        severity="red",
        category="privacy",
    )
    _add_match(
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
        if level not in levels:
            continue
        _add_match(
            warnings,
            text=text,
            pattern=re.compile(pattern, re.IGNORECASE),
            warning_id=warning_id,
            label=label,
            severity=severity,
            category="ai_act",
        )

    if any(w["severity"] == "red" for w in warnings):
        status = "red"
    elif any(w["severity"] == "orange" for w in warnings):
        status = "orange"
    elif any(w["severity"] == "yellow" for w in warnings):
        status = "yellow"
    else:
        status = "green"
    risk_score = {"green": 0, "yellow": 35, "orange": 65, "red": 95}[status]
    return {
        "status": status,
        "risk_score": risk_score,
        "should_block": status == "red",
        "should_warn": status in {"yellow", "orange"},
        "level": level,
        "check_type": check_type,
        "warnings": warnings[:25],
    }
