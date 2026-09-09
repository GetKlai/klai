"""Localized refusal copy for safety blocks."""

from __future__ import annotations

# Neutral refusal: do NOT enumerate the blocked categories. Echoing
# "weapons, explosives, ..." back into the chat puts those exact terms in
# the conversation history, which (a) reads as if Klai assumed the worst of
# the user and (b) re-poisons any downstream scan of that history.
_REFUSAL_NL = "Ik kan hierop geen antwoord geven."
_REFUSAL_EN = "I can't help with that request."


def refusal_message(language: str | None = None, reason: str = "") -> str:
    """Return the refusal copy for a language CODE — never raw user text.

    ``"nl"`` picks Dutch, and so does "no decision" (``None``, ``""`` or
    ``"und"`` — the identifier's undetermined code): the same fallback as
    every other rendered string
    (``klai_chat_prompts._language_is_dutch``) — Klai's customers are
    overwhelmingly Dutch, so an undecided turn is likelier Dutch than
    English. Any other explicit code picks English.

    This library identifies no language itself: it stays dependency-free.
    Callers pass the code from the shared conversation-language identifier
    (``identify_text_language`` / ``resolve_conversation_language``).
    """
    _ = reason
    value = (language or "").strip()
    if not value or value in ("nl", "und"):
        return _REFUSAL_NL
    return _REFUSAL_EN
