"""Klai's Dutch/credential recognizer pack for Presidio (SPEC-PRIVACY-MISTRAL-PII-001
Phase 1, REQ-3/REQ-4).

Every recognizer here is regex-plus-checksum, never NLP-model-based. Per REQ-2 that
makes them **jurisdiction-specific, not language-specific**: a BSN in an English
sentence is still a BSN. This module registers each class once per language via
the analyzer's YAML `recognizer_registry` (see ``conf/analyzer.yaml``) rather than
gating detection on ``presidio_language`` — the recognizer objects are identical
across languages, only ``supported_language`` differs, exactly mirroring how
Presidio's own multi-language predefined recognizers (e.g. ``CreditCardRecognizer``)
are declared.

Loading contract: this module must be imported (registering these classes as
``EntityRecognizer`` subclasses, discoverable by
``RecognizerListLoader.get_existing_recognizer_cls``) BEFORE
``AnalyzerEngineProvider(...).create_engine()`` runs. ``sitecustomize.py`` does
that import at interpreter start, so the stock ``app.py`` needs no changes.

REQ-3: checksum-validated recognizers are Python ``PatternRecognizer`` subclasses
overriding ``validate_result()`` — YAML's registry can express a regex and a score
but not a checksum, and for BSN the checksum is the whole point: a bare nine-digit
pattern matches order numbers and customer references, and the elfproef is what
makes it a control instead of a nuisance. Only ``NLBSNRecognizer`` has a real
checksum among the four Klai-authored entities here (KvK/BTW/postcode are format
recognizers per the SPEC's own table); it is still implemented as a Python class,
consistent with the rest of the pack and REQ-3's "not YAML entries" instruction.
"""

from __future__ import annotations

from typing import List, Optional

from presidio_analyzer import EntityRecognizer, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.predefined_recognizers import PhoneRecognizer

# ---------------------------------------------------------------------------
# NL_BSN — elfproef (weighted sum mod 11)
# ---------------------------------------------------------------------------
# PORTED, not rewritten, from klai-portal/backend/app/services/shield_compliance.py
# :96-104 (`_valid_bsn`) — that implementation is already working in production
# (SPEC-SHIELD-001). Only the surrounding class scaffolding is new.


def _valid_bsn(value: str) -> bool:
    """Elfproef checksum. Verbatim port of shield_compliance.py's `_valid_bsn`."""
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 8:
        digits = "0" + digits
    if len(digits) != 9:
        return False
    checksum = sum((9 - index) * int(digit) for index, digit in enumerate(digits[:8]))
    checksum -= int(digits[-1])
    return checksum % 11 == 0


class NLBSNRecognizer(PatternRecognizer):
    """Dutch BSN (burgerservicenummer), validated with the elfproef.

    A bare 8-9 digit run is a common shape (order numbers, customer refs), so the
    regex alone is deliberately weak (0.3) and `validate_result()` is what turns a
    candidate into a control: a failing elfproef drops the score to
    ``EntityRecognizer.MIN_SCORE`` and the result is not returned at all (AC-3).
    """

    PATTERNS = [
        # 9 digits is the canonical BSN and stands on the elfproef alone.
        Pattern("NL BSN (candidate, 9 digits)", r"(?<!\d)\d{9}(?!\d)", 0.3),
        # 8 digits is the legacy form with the leading zero omitted. It is
        # ALSO the shape of a YYYYMMDD date, an order number and a customer
        # reference — and ~9% of them pass the padded elfproef (measured over
        # every date 2020-2030: 365 of 4018). On the Mistral path NL_BSN is
        # masked for every org and NEVER restored, so a false positive here
        # silently destroys "Factuurdatum 20200201" with no way back. That is
        # a worse outcome than missing a legacy 8-digit BSN, so this form now
        # requires a BSN context word nearby (see analyze()).
        Pattern("NL BSN (candidate, 8 digits, needs context)", r"(?<!\d)\d{8}(?!\d)", 0.3),
    ]

    def __init__(
        self,
        patterns: Optional[List[Pattern]] = None,
        context: Optional[List[str]] = None,
        supported_language: str = "en",
        supported_entity: str = "NL_BSN",
        name: Optional[str] = None,
    ):
        super().__init__(
            supported_entity=supported_entity,
            patterns=patterns or self.PATTERNS,
            context=context,
            supported_language=supported_language,
            name=name,
        )

    def validate_result(self, pattern_text: str) -> Optional[bool]:
        return _valid_bsn(pattern_text)

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        """Drop 8-digit matches that have no BSN context word nearby.

        The elfproef alone is not enough for the 8-digit form: it is the
        same shape as a YYYYMMDD date, and ~9% of dates pass it. Since
        NL_BSN is masked for every org and never restored, an unqualified
        8-digit match irreversibly destroys ordinary business text. Nine
        digits is the canonical form and keeps standing on the checksum
        alone, so real BSNs written in full are unaffected.
        """
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        if not results:
            return results
        kept = []
        for result in results:
            digits = text[result.start : result.end]
            if len(digits.strip()) > 8:
                kept.append(result)
                continue
            window = text[
                max(0, result.start - _BSN_CONTEXT_WINDOW) : result.end
                + _BSN_CONTEXT_WINDOW
            ].lower()
            if any(word in window for word in _BSN_CONTEXT_WORDS):
                kept.append(result)
        return kept


# ---------------------------------------------------------------------------
# NL_KVK — 8 digits + nearby context words ("kvk", "handelsregister")
# ---------------------------------------------------------------------------
# No checksum exists for a KvK number, so REQ-3's "context words" column is the
# actual gate here. Rather than depend on Presidio's built-in lemma-based context
# enhancer (which needs `nlp_artifacts.lemmas` from the NLP engine — meaningless
# for the blank/tokenizer-only pipelines this pack runs under, see REQ-2), this
# recognizer does its own text-window context check in `analyze()`. That keeps it
# self-contained and NLP-engine-independent, in the same spirit as the checksum
# recognizers: a plain regex/text check, not a model dependency.

_BSN_CONTEXT_WORDS = ("bsn", "burgerservicenummer", "sofinummer", "sofi-nummer")
_BSN_CONTEXT_WINDOW = 40

_KVK_CONTEXT_WORDS = ("kvk", "handelsregister")
_KVK_CONTEXT_WINDOW = 40


class NLKvKRecognizer(PatternRecognizer):
    """Dutch KvK (Chamber of Commerce) number: 8 digits, gated on nearby context."""

    PATTERNS = [
        Pattern("NL KvK (candidate)", r"(?<!\d)\d{8}(?!\d)", 0.3),
    ]

    def __init__(
        self,
        patterns: Optional[List[Pattern]] = None,
        context: Optional[List[str]] = None,
        supported_language: str = "en",
        supported_entity: str = "NL_KVK",
        name: Optional[str] = None,
    ):
        super().__init__(
            supported_entity=supported_entity,
            patterns=patterns or self.PATTERNS,
            context=context or list(_KVK_CONTEXT_WORDS),
            supported_language=supported_language,
            name=name,
        )

    def analyze(
        self,
        text: str,
        entities: List[str],
        nlp_artifacts=None,
        regex_flags: Optional[int] = None,
    ) -> List[RecognizerResult]:
        candidates = super().analyze(text, entities, nlp_artifacts, regex_flags)
        confirmed = []
        for result in candidates:
            window = text[
                max(0, result.start - _KVK_CONTEXT_WINDOW) : result.end
                + _KVK_CONTEXT_WINDOW
            ].lower()
            if any(word in window for word in _KVK_CONTEXT_WORDS):
                result.score = EntityRecognizer.MAX_SCORE
                confirmed.append(result)
        return confirmed


# ---------------------------------------------------------------------------
# NL_BTW — "NL" + 9 digits + "B" + 2 digits (format only, per the SPEC's table)
# ---------------------------------------------------------------------------


class NLBTWRecognizer(PatternRecognizer):
    """Dutch VAT (BTW) number: NLddddddddd B dd. Format recognizer, no checksum."""

    PATTERNS = [
        Pattern("NL BTW", r"\bNL\d{9}B\d{2}\b", 0.7),
    ]

    def __init__(
        self,
        patterns: Optional[List[Pattern]] = None,
        context: Optional[List[str]] = None,
        supported_language: str = "en",
        supported_entity: str = "NL_BTW",
        name: Optional[str] = None,
    ):
        super().__init__(
            supported_entity=supported_entity,
            patterns=patterns or self.PATTERNS,
            context=context,
            supported_language=supported_language,
            name=name,
        )


# ---------------------------------------------------------------------------
# NL_POSTCODE — "1234 AB" shape
# ---------------------------------------------------------------------------
# Pattern reused from shield_compliance.py:38 (`_NL_POSTCODE_RE`).


class NLPostcodeRecognizer(PatternRecognizer):
    """Dutch postcode: 4 digits (not starting with 0) + 2 letters."""

    PATTERNS = [
        # (?-i:...) forces case-sensitivity for the letter pair only. The
        # registry applies IGNORECASE globally, which turned [A-Z]{2} into
        # "any two letters" and matched "2026 en" / "1500 op" in ordinary
        # Dutch. A real postcode is uppercase; a year followed by a
        # two-letter word is not one, and masking it hides the year from the
        # model even though the user gets it back.
        Pattern("NL postcode", r"\b[1-9][0-9]{3}\s?(?-i:[A-Z]{2})\b", 0.4),
    ]

    def __init__(
        self,
        patterns: Optional[List[Pattern]] = None,
        context: Optional[List[str]] = None,
        supported_language: str = "en",
        supported_entity: str = "NL_POSTCODE",
        name: Optional[str] = None,
    ):
        super().__init__(
            supported_entity=supported_entity,
            patterns=patterns or self.PATTERNS,
            context=context,
            supported_language=supported_language,
            name=name,
        )


# ---------------------------------------------------------------------------
# SECRET — REQ-4: PEM private-key blocks, JWTs, Bearer values, provider key prefixes
# ---------------------------------------------------------------------------
# The PEM pattern deliberately makes the "<type> " token optional in BOTH the
# BEGIN and END markers: canonical PKCS#8 keys are literally
# "-----BEGIN PRIVATE KEY-----" with no type, and requiring one would let the
# most common key format through unmasked. `.*?` (non-greedy) + the registry's
# default global_regex_flags (DOTALL|MULTILINE|IGNORECASE, see conf/analyzer.yaml)
# lets the span run across newlines to the matching END marker, so key material
# is never left in the payload. A BEGIN with no following END never matches at
# all — "bare unmatched PEM header" (AC-5) stays undetected by design, since a
# half pattern is not sensitive key material.

_SECRET_PATTERNS = [
    Pattern(
        "PEM private key block",
        r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----.*?-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
        0.95,
    ),
    Pattern(
        "JWT",
        r"\bey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
        0.9,
    ),
    Pattern(
        "Authorization Bearer header",
        # RFC 6750's b64token alphabet is ALPHA / DIGIT / "-" / "." / "_" /
        # "~" / "+" / "/", optionally "="-padded. The original char class
        # dropped "~", "+", "/" — real base64(url) bearer tokens routinely
        # contain them and would have gone through unmasked (sol-review).
        # Anchored to an actual Authorization header. Unanchored, and with
        # the registry's default IGNORECASE, this matched ordinary Dutch
        # prose: "De bearer verantwoordelijkheid ligt bij de klant" ->
        # SECRET 'bearer verantwoordelijkheid'. SECRET is masked for every
        # org and NEVER restored, so that silently deleted two words of a
        # user's sentence with no way back. Bare tokens are still covered by
        # the JWT and provider-key patterns, so anchoring loses no real
        # credential.
        r"Authorization\s*:\s*Bearer\s+[A-Za-z0-9\-_.~+/=]{10,}",
        0.85,
    ),
    Pattern(
        "Provider API key prefix",
        # sk-[A-Za-z0-9]{16,} only matched OpenAI's old flat key shape.
        # Current provider keys are hyphen-segmented — Anthropic
        # `sk-ant-api03-...`, OpenAI project/service-account keys
        # `sk-proj-...` / `sk-svcacct-...` — and none of those reach 16
        # consecutive alnum characters before the first internal hyphen, so
        # the old pattern silently let them through (sol-review). Allowing
        # `-`/`_` in the body catches both the old and current shapes.
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b",
        0.9,
    ),
]


class SecretRecognizer(PatternRecognizer):
    """Credentials: PEM private keys, JWTs, Bearer headers, provider key prefixes."""

    PATTERNS = _SECRET_PATTERNS

    def __init__(
        self,
        patterns: Optional[List[Pattern]] = None,
        context: Optional[List[str]] = None,
        supported_language: str = "en",
        supported_entity: str = "SECRET",
        name: Optional[str] = None,
    ):
        super().__init__(
            supported_entity=supported_entity,
            patterns=patterns or self.PATTERNS,
            context=context,
            supported_language=supported_language,
            name=name,
        )


KLAI_RECOGNIZER_CLASSES = (
    NLBSNRecognizer,
    NLKvKRecognizer,
    NLBTWRecognizer,
    NLPostcodeRecognizer,
    SecretRecognizer,
)


# ---------------------------------------------------------------------------
# NL_PHONE — stock PhoneRecognizer with regions that actually apply
# ---------------------------------------------------------------------------
# REQ-3 calls for "phone with region NL". The YAML registry silently drops a
# `supported_regions:` key on a `type: predefined` entry — it never reaches
# PhoneRecognizer.__init__ — so the deployed recognizer ran with Presidio's
# DEFAULT_SUPPORTED_REGIONS instead:
#
#     PhoneRecognizer lang=nl regions=('US','UK','DE','FE','IL','IN','CA','BR')
#
# (verified by introspecting the running container, 2026-08-20). NL is absent
# from that list, so Dutch phone detection was working *by accident*: most
# Dutch numbers happen to also parse as valid German ones. Rotterdam's 010
# range has no German equivalent, so `010-7654321` was never detected in any
# format — which is what the Phase 0 run surfaced as six undetected numbers.
#
# Subclassing is how the other Klai recognizers already reach the registry, and
# it puts the region list somewhere a YAML loader cannot quietly ignore.
#
# The stock defaults are kept alongside NL and BE rather than replaced: Klai is
# language-agnostic and a tenant's documents legitimately contain foreign
# numbers, so narrowing to NL alone would trade one detection gap for another.
class NLPhoneRecognizer(PhoneRecognizer):
    """PhoneRecognizer with NL (and BE) in the region list.

    Neighbouring-country numbers are realistic in Dutch SMB correspondence, so
    BE is included; the stock regions are retained so this can only ever detect
    more than the recognizer it replaces, never less.
    """

    KLAI_SUPPORTED_REGIONS = ("NL", "BE") + PhoneRecognizer.DEFAULT_SUPPORTED_REGIONS

    def __init__(self, **kwargs):
        kwargs.setdefault("supported_regions", self.KLAI_SUPPORTED_REGIONS)
        super().__init__(**kwargs)
