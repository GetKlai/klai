"""Deterministic baseline policy for LLM input/context/output safety."""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata

from klai_llm_safety.models import SafetyCategory, SafetyDecision, SafetyRequest
from klai_llm_safety.refusals import refusal_message

_LEET_TABLE = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "@": "a",
        "$": "s",
    }
)

# Homoglyph fold: confusable look-alike letters → ASCII Latin. Covers the
# bypass class where an attacker writes ``[ＳＴＡＲＴ ＯＵＴＰＵＴ]`` or
# ``GODMODE`` with Cyrillic ``о``/``с``/``е`` to evade the prompt-injection
# regex while the model still reads it as the intended ASCII string. Not
# exhaustive — only the lower/upper Cyrillic and Greek letters that share a
# glyph with ASCII Latin. NFKC already collapses fullwidth and most
# compatibility variants, so we don't repeat those here.
_HOMOGLYPH_TABLE = str.maketrans(
    {
        # Cyrillic lowercase → Latin
        "а": "a",
        "е": "e",
        "і": "i",
        "ј": "j",
        "о": "o",
        "р": "p",
        "с": "c",
        "ѕ": "s",
        "у": "y",
        "х": "x",
        "ԁ": "d",
        # Cyrillic uppercase → Latin
        "А": "A",
        "В": "B",
        "Е": "E",
        "І": "I",
        "К": "K",
        "М": "M",
        "Н": "H",
        "О": "O",
        "Р": "P",
        "С": "C",
        "Т": "T",
        "У": "Y",
        "Х": "X",
        # Greek lowercase → Latin. ``ς`` (U+03C2 final sigma) is included
        # because NFKC folds ``ϲ`` (U+03F2 lunate sigma) into ``ς`` before
        # our table runs, so the ``ϲ → c`` mapping alone would miss it.
        "α": "a",
        "ο": "o",
        "ι": "i",
        "ρ": "p",
        "ϲ": "c",
        "ς": "c",
        "ν": "v",
        # Greek uppercase → Latin
        "Α": "A",
        "Β": "B",
        "Ε": "E",
        "Ζ": "Z",
        "Η": "H",
        "Ι": "I",
        "Κ": "K",
        "Μ": "M",
        "Ν": "N",
        "Ο": "O",
        "Ρ": "P",
        "Τ": "T",
        "Χ": "X",
        # Common typographic substitutions for separators inside markers
        # like ``[START—OUTPUT]`` or ``[START_OUTPUT]``.
        "—": " ",
        "–": " ",
        "_": " ",
    }
)

# Pure-base64 block heuristic: any run of >=64 base64-alphabet characters
# without whitespace. 64 chars is comfortably above standard short identifiers
# (UUIDs, JWT segments, base64-encoded SHA256 fingerprints are typically <60)
# yet small enough to catch a one-sentence jailbreak prompt that decodes to
# ~48 bytes of cleartext.
_BASE64_BLOCK_RE = re.compile(r"[A-Za-z0-9+/]{64,}={0,2}")

_PROMPT_INJECTION_RE = re.compile(
    r"(?i)(?:"
    r"\[(?:start|end)\s+output\]|"
    r"\[/?inst\]|<\|im_start\|>|<\|im_end\|>|"
    r"\bgodmode\b|\bdan\s+(?:mode|jailbreak|prompt)\b|\bdo\s+anything\s+now\b|"
    r"\bdeveloper\s+mode\b|\bjailbreak\b|"
    r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions\b|"
    r"\bdisregard\s+(?:all\s+)?(?:previous|prior|above)\s+instructions\b|"
    r"\bnow\s+output\s+format\b|"
    r"\bunrestricted(?:ly)?\b|\bliberated\s+response\b|"
    r"\brefusal\b.*\bbypass\b"
    r")"
)

_SYSTEM_PROMPT_EXTRACTION_RE = re.compile(
    r"(?i)\b(?:"
    r"show|print|reveal|dump|repeat|verbatim|exfiltrate"
    r")\b.{0,80}\b(?:"
    r"system prompt|developer message|hidden instructions|internal instructions|beleid|systeemprompt"
    r")\b"
)

_HAZARDOUS_TOPIC_RE = re.compile(
    r"(?i)\b(?:"
    r"c-?4|rdx|tnt|dynamite|explosive(?:s)?|explosief|explosieven|"
    r"bomb(?:s)?|bom(?:men)?|improvised\s+explosive|ied|"
    r"sarin|ricin|anthrax|nerve\s+gas|mosterdgas"
    r")\b"
)

_HAZARDOUS_INSTRUCTION_RE = re.compile(
    r"(?i)\b(?:"
    r"how\s+(?:do\s+i\s+)?(?:to\s+)?(?:make|build|create|synthesi[sz]e|manufacture)|"
    r"recipe|step(?:-| )?by(?:-| )?step|instructions?|precursor(?:s)?|synthesis|"
    r"maak(?:\s+ik)?|maken|bouw(?:en)?|recept|stappenplan|handleiding|"
    r"ingredients?|benodigdheden|chemistry|detonat(?:e|or|ion)|ontstek(?:er|en)"
    r")\b"
)

_ENCODED_WRAPPER_RE = re.compile(
    r"(?i)\b(?:base64|rot13|hex|unicode|decode|encoded|gecodeerd)\b.{0,80}\b(?:"
    r"instructions?|prompt|payload|query|vraag"
    r")\b"
)


def _normalize(text: str, *, fold_leet: bool = False) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    # Homoglyph fold is unconditional: it costs one translate() and closes
    # the Cyrillic/Greek-look-alike bypass class against every regex below.
    normalized = normalized.translate(_HOMOGLYPH_TABLE)
    if fold_leet:
        normalized = normalized.translate(_LEET_TABLE)
    return re.sub(r"\s+", " ", normalized).strip()


def _contains_hazardous_instructions(text: str) -> bool:
    return bool(_HAZARDOUS_TOPIC_RE.search(text) and _HAZARDOUS_INSTRUCTION_RE.search(text))


def _decoded_base64_blocks(text: str) -> list[str]:
    """Return successfully-decoded UTF-8 contents of every base64 block.

    The model can read a base64 payload the same way it reads plaintext, so a
    decoded block that fires ``_PROMPT_INJECTION_RE`` or the hazardous-pair
    regex is treated as if the user had written it directly. Blocks that
    don't decode to valid UTF-8 (binary, random alphabet noise) are skipped
    silently — those won't influence the model's behaviour either.
    """
    decoded: list[str] = []
    for match in _BASE64_BLOCK_RE.finditer(text):
        block = match.group(0)
        # ``validate=True`` rejects blocks that look base64-ish but contain
        # stray characters — fewer false positives.
        try:
            raw = base64.b64decode(block, validate=True)
        except (binascii.Error, ValueError):
            continue
        try:
            decoded.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return decoded


def check_text(request: SafetyRequest) -> SafetyDecision:
    """Evaluate text with deterministic baseline policy.

    This function does not call any external provider. Ambiguous encoded-wrapper
    cases return NEEDS_PROVIDER so callers can route to a classifier when one is
    configured or apply their surface-specific fallback.
    """
    text = _normalize(request.text)
    folded_text = _normalize(request.text, fold_leet=True)
    if not text:
        return SafetyDecision.allow()

    # Decoded base64 payloads are scanned with the same regexes as plaintext
    # so an attacker can't ship ``aG93IHRvIG1ha2UgVE5U`` and dodge the
    # prompt-injection filter. We add the decoded blocks to the search
    # surface — they're already normalized through ``_normalize`` so they
    # also benefit from the homoglyph + leet folds.
    decoded_blocks = [
        _normalize(block) for block in _decoded_base64_blocks(request.text)
    ]
    decoded_blocks_folded = [
        _normalize(block, fold_leet=True) for block in _decoded_base64_blocks(request.text)
    ]

    def _any(pattern: re.Pattern[str]) -> bool:
        if pattern.search(text) or pattern.search(folded_text):
            return True
        return any(pattern.search(block) for block in (*decoded_blocks, *decoded_blocks_folded))

    categories: list[SafetyCategory] = []

    hazardous = (
        _contains_hazardous_instructions(text)
        or _contains_hazardous_instructions(folded_text)
        or any(_contains_hazardous_instructions(block) for block in decoded_blocks)
        or any(_contains_hazardous_instructions(block) for block in decoded_blocks_folded)
    )
    prompt_injection = _any(_PROMPT_INJECTION_RE)
    system_prompt_extraction = _any(_SYSTEM_PROMPT_EXTRACTION_RE)
    encoded_wrapper = _any(_ENCODED_WRAPPER_RE)

    if hazardous:
        categories.append(SafetyCategory.HAZARDOUS_INSTRUCTIONS)
        if prompt_injection:
            categories.append(SafetyCategory.PROMPT_INJECTION)
            reason = "prompt_injection_hazardous_content"
        else:
            reason = "hazardous_instruction_content"
        return SafetyDecision.block(
            reason=reason,
            categories=tuple(categories),
            safe_replacement=refusal_message(request.locale_hint or request.text, reason),
        )

    if prompt_injection:
        return SafetyDecision.block(
            reason="prompt_injection_pattern",
            categories=(SafetyCategory.PROMPT_INJECTION,),
            confidence=0.95,
        )

    if system_prompt_extraction:
        return SafetyDecision.block(
            reason="system_prompt_extraction_request",
            categories=(SafetyCategory.SYSTEM_PROMPT_EXTRACTION,),
            confidence=0.95,
        )

    if encoded_wrapper:
        return SafetyDecision.needs_provider(
            reason="encoded_wrapper_needs_provider",
            categories=(SafetyCategory.ENCODED_WRAPPER,),
        )

    return SafetyDecision.allow()
