"""Localized refusal copy for safety blocks."""

from __future__ import annotations

import re

_REFUSAL_NL = "Ik kan niet helpen met instructies voor wapens, explosieven of andere gevaarlijke materialen."
_REFUSAL_EN = "I can't help with instructions for weapons, explosives, or other dangerous materials."


def _looks_dutch(text: str) -> bool:
    return bool(re.search(r"(?i)\b(?:hoe|maak|maken|bom|explosief|ik|niet|kan|met)\b", text or ""))


def refusal_message(locale_or_text: str = "", reason: str = "") -> str:
    _ = reason
    value = (locale_or_text or "").strip().lower()
    if value.startswith("nl") or _looks_dutch(locale_or_text):
        return _REFUSAL_NL
    return _REFUSAL_EN
