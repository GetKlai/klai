"""Regression tests for SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT.

The expressive register of the public help-page widget (docs/research/
voys-tone-of-voice.md § 6: the brand demonstrably runs two registers — the
dry help-article one and a higher marketing/blog one). This profile is the
second; SUPPORT_CHAT_SYSTEM_PROMPT stays the first and the default.

Two contracts are guarded here:

1. The register swap is confined to tone. The body must equal SUPPORT
   section-for-section, byte for byte, except ## Tone (the swap) plus one
   added guard section. That is what makes "register changes tone, never
   truth" mechanical rather than aspirational: the source rules, the no-
   promises rule, escalation, anti-fabrication and the missing-answer
   behaviour are the same bytes as in the restrained profile.
2. Adding this profile did not touch the existing ones. The five customer-
   facing and internal profiles are pinned by SHA-256 over their final
   values — not by diffing source — so any edit that shifts even one
   whitespace character in GROUNDED / GENERAL / OPEN_KB / META / SUPPORT
   (or SUPPORT_BROAD) fails here with the changed name in the message.
"""

from __future__ import annotations

import hashlib
import re

import pytest

from klai_chat_prompts import (
    GENERAL_CHAT_SYSTEM_PROMPT,
    GROUNDED_CHAT_SYSTEM_PROMPT,
    META_CHAT_SYSTEM_PROMPT,
    OPEN_KB_CHAT_SYSTEM_PROMPT,
    SUPPORT_BROAD_CHAT_SYSTEM_PROMPT,
    SUPPORT_CHAT_SYSTEM_PROMPT,
    SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT,
)

# Sections allowed to differ between SUPPORT and SUPPORT_EXPRESSIVE.
_TONE_SECTION = "## Tone"
_REGISTER_SECTION = "## Register changes tone, never truth"


def _split_sections(prompt: str) -> tuple[str, dict[str, str]]:
    """Split a support profile into (intro, {section heading: full text})."""
    marker = "You are the AI support assistant"
    assert marker in prompt
    body = prompt[prompt.index(marker) :]
    parts = re.split(r"(?=^## )", body, flags=re.M)
    return parts[0], {part.splitlines()[0]: part for part in parts[1:]}


# ─── structural invariants ───────────────────────────────────────────────


def test_expressive_prompt_is_non_empty():
    assert isinstance(SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT, str)
    assert len(SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT) > 500


def test_expressive_prompt_carries_critical_marker_and_identity():
    assert "[CRITICAL]" in SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT
    assert "AI support assistant" in SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT
    assert "Klai" not in SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT


def test_expressive_prompt_shares_language_preamble_byte_for_byte():
    # Same rule as every other profile: the three language guards live in
    # one private constant and may never drift between modes.
    preamble_end = GROUNDED_CHAT_SYSTEM_PROMPT.find("\n\nYou are Klai AI")
    assert preamble_end > 0, "GROUNDED prompt structure changed unexpectedly"
    grounded_preamble = GROUNDED_CHAT_SYSTEM_PROMPT[:preamble_end]
    assert SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT.startswith(grounded_preamble + "\n\n")


# ─── the register swap is confined to tone ───────────────────────────────


def test_expressive_prompt_differs_from_support_only_in_tone_and_guard():
    support_intro, support_sections = _split_sections(SUPPORT_CHAT_SYSTEM_PROMPT)
    expressive_intro, expressive_sections = _split_sections(SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT)

    assert support_intro == expressive_intro, "the identity/grounding intro must not change with the register"
    added = set(expressive_sections) - set(support_sections)
    removed = set(support_sections) - set(expressive_sections)
    assert added == {_REGISTER_SECTION}
    assert removed == set()

    for heading, support_text in support_sections.items():
        if heading == _TONE_SECTION:
            continue
        assert expressive_sections[heading] == support_text, (
            f"{heading} diverged between the restrained and the expressive profile; "
            "only ## Tone may differ — the register must not touch truth rules."
        )
    # The tone section itself must actually be the one that changed.
    assert expressive_sections[_TONE_SECTION] != support_sections[_TONE_SECTION]


def test_expressive_tone_adds_personality_within_limits():
    text = SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT
    lowered = text.lower()
    # More personality and warmth than the help-article register.
    assert "expressive register" in lowered
    assert "personality and warmth" in lowered
    # A witty remark is allowed, capped, and never on sensitive turns.
    assert "witty remark" in lowered
    assert "at most one" in lowered
    for never_case in ("outage", "complaint", "bill"):
        assert never_case in lowered
    # Functional emoji yes, decorative no.
    assert "⚠️" in text
    assert "emoji are allowed only where one carries meaning" in lowered
    assert "no decorative emoji" in lowered
    # The opening may be livelier; the structure around it must not move.
    assert "greeting on the first reply may be livelier" in lowered
    assert "never chatty or salesy" in lowered
    assert "no exclamation-mark chains" in lowered


def test_restrained_no_emoji_rule_does_not_leak_into_expressive():
    # The restrained profile bans emoji outright ("No emoji, ..."); the
    # expressive one replaces that with the functional-only rule. If the
    # ban ever survives next to the permission, the two contradict.
    tone = _split_sections(SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT)[1][_TONE_SECTION]
    assert "No emoji" not in tone


def test_expressive_prompt_keeps_the_measured_voice_invariants():
    # Tone work aside, the brand rules from the restrained profile stand:
    # je/jij never u, the Dutch phrasing set, the friend test.
    text = SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT
    assert "je/jij, never u" in text
    assert "Dit kan even duren" in text
    assert "Goed om te weten" in text
    assert "The friend test" in text
    assert "zou je dit tegen een vriend zeggen" in text


# ─── the guard section: tone never overrides truth ───────────────────────


def test_expressive_prompt_states_that_register_changes_tone_never_truth():
    guard = _split_sections(SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT)[1][_REGISTER_SECTION]
    lowered = guard.lower()
    assert "never changes what is true" in lowered
    # It names each family of rules that must survive the register.
    for pinned in (
        "help-article chunks",
        "citation markers",
        "promise anything on behalf",
        "transfer this chat to a person",
        "never guess",
        "when the help articles do not answer",
    ):
        assert pinned in lowered, f"register guard does not pin {pinned!r}"
    # And it resolves any perceived conflict in favour of the rule.
    assert "the rule wins" in lowered


def test_expressive_prompt_names_kennisbank_only_as_a_prohibition():
    # Same rule as the restrained profile: the jargon may appear only in
    # the instruction that forbids it in front of a visitor.
    for line in SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT.splitlines():
        lowered = line.lower()
        if "kennisbank" in lowered or "knowledge base" in lowered:
            assert "do not use" in lowered, f"kennisbank/knowledge base leaked outside the prohibition: {line!r}"


def test_expressive_prompt_keeps_the_hard_rules_present():
    # Cheap direct checks of the rules the guard section points at, so a
    # rename in either place trips a named test rather than only the
    # structural comparison.
    text = SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT
    assert "Do NOT write citation markers" in text
    assert "No promises on behalf of the company" in text
    assert "Do NOT commit to" in text
    assert "cannot transfer this chat to a person" in text
    assert "AT MOST ONE short clarifying question" in text
    assert "Ik vind dit niet terug in onze helpartikelen" in text
    assert "I can't find this in our help articles" in text


# ─── the existing profiles stayed untouched ──────────────────────────────

# SHA-256 of each profile's final value, recorded before the expressive
# register was added. Pinned on the VALUES, not on the source: an edit that
# reflows a line, tweaks a word, or accidentally folds the new tone into an
# existing profile fails here naming exactly the profile that moved.
_PROFILE_HASHES = {
    "GROUNDED_CHAT_SYSTEM_PROMPT": "6283830328308dfaa20d04732ab851775cd0fb7ae7d92f583a3ae36d85dd3c00",
    "GENERAL_CHAT_SYSTEM_PROMPT": "e51d3bba78ecfcd4c26c8553935756f03afe7d9284f1efd4b44f6d962d62f38a",
    "OPEN_KB_CHAT_SYSTEM_PROMPT": "2babfcab82348a9ddc90c21e48bc0e7de34a16dee20938fceac7ef9b4f02221b",
    "META_CHAT_SYSTEM_PROMPT": "1123025a36e42e114461db2730421de5f44e686b063a77ba6894fdfd7599bcf5",
    # re-baselined 2026-09-09: escalation section — a matching article no
    # longer cancels the appointment offer. Deliberate, and the only profile
    # that moved — the four unrelated profiles above and SUPPORT_BROAD below
    # keep their original digests.
    "SUPPORT_CHAT_SYSTEM_PROMPT": "a4a551d0b1ed9ce5b71fc57868a27212158ef3754e49199faf2489ffb78b16d2",
    "SUPPORT_BROAD_CHAT_SYSTEM_PROMPT": "a8595ff04ff34cad9deb52eaca21e0961f8ad2ec1ca84f2b4f8df50d64fffbd1",
}
_PROFILE_VALUES = {
    "GROUNDED_CHAT_SYSTEM_PROMPT": GROUNDED_CHAT_SYSTEM_PROMPT,
    "GENERAL_CHAT_SYSTEM_PROMPT": GENERAL_CHAT_SYSTEM_PROMPT,
    "OPEN_KB_CHAT_SYSTEM_PROMPT": OPEN_KB_CHAT_SYSTEM_PROMPT,
    "META_CHAT_SYSTEM_PROMPT": META_CHAT_SYSTEM_PROMPT,
    "SUPPORT_CHAT_SYSTEM_PROMPT": SUPPORT_CHAT_SYSTEM_PROMPT,
    "SUPPORT_BROAD_CHAT_SYSTEM_PROMPT": SUPPORT_BROAD_CHAT_SYSTEM_PROMPT,
}


@pytest.mark.parametrize("name", sorted(_PROFILE_HASHES))
def test_existing_profile_is_byte_for_byte_unchanged(name: str):
    digest = hashlib.sha256(_PROFILE_VALUES[name].encode()).hexdigest()
    assert digest == _PROFILE_HASHES[name], (
        f"{name} changed. The expressive register may only ADD "
        "SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT; if this change is truly "
        "intended, re-baseline the hash deliberately and say so in the PR."
    )


def test_expressive_wording_did_not_leak_into_the_restrained_profiles():
    # The register vocabulary must stay confined to the expressive profile
    # and its guard section — SUPPORT and SUPPORT_BROAD keep sounding like
    # the help articles.
    for text in (SUPPORT_CHAT_SYSTEM_PROMPT, SUPPORT_BROAD_CHAT_SYSTEM_PROMPT):
        for leak in ("Expressive register", "Register changes tone", "witty remark", "decorative emoji"):
            assert leak not in text, f"{leak!r} leaked into a restrained profile"
