"""Localized refusal copy for safety blocks."""

from __future__ import annotations

import re

# Neutral refusal: do NOT enumerate the blocked categories. Echoing
# "weapons, explosives, ..." back into the chat puts those exact terms in
# the conversation history, which (a) reads as if Klai assumed the worst of
# the user and (b) re-poisons any downstream scan of that history.
_REFUSAL_NL = "Ik kan hierop geen antwoord geven."
_REFUSAL_EN = "I can't help with that request."


def _looks_dutch(text: str) -> bool:
    return bool(re.search(r"(?i)\b(?:hoe|maak|maken|bom|explosief|ik|niet|kan|met)\b", text or ""))


def refusal_message(locale_or_text: str = "", reason: str = "") -> str:
    _ = reason
    value = (locale_or_text or "").strip().lower()
    if value.startswith("nl") or _looks_dutch(locale_or_text):
        return _REFUSAL_NL
    return _REFUSAL_EN
