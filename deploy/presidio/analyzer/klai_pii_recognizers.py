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

# The Dutch words were the whole gate until 2026-08-25, which made this
# entity silently language-bound rather than jurisdiction-bound: a tenant
# writing "our chamber of commerce number is 12345678" in English got no
# match, with the box ticked. The additions are the same concept in the
# languages the analyzer serves, plus the Belgian and German registry names.
# Deliberately NOT added: bare "company number" and "registratienummer".
# Both attach to any 8-digit run in ordinary business text, and the gate is
# the only thing standing between this pattern and every order number.
_KVK_CONTEXT_WORDS = (
    "kvk",
    "handelsregister",          # NL + DE registry, same word
    "handelsregisternummer",
    "chamber of commerce",
    "companies house",
    "ondernemingsnummer",       # BE (nl)
    "numéro d'entreprise",      # BE (fr)
    "kbo",
    "bce",
)
_KVK_CONTEXT_WINDOW = 40


def _valid_be_enterprise_number(value: str) -> bool:
    """Belgian enterprise number (KBO/BCE) modulo-97 check.

    Ten digits, the first being 0 or 1, the last two a check number equal to
    ``97 - (first eight mod 97)``. Cross-checked against two independent
    sources on 2026-08-25 rather than written from memory, because a wrong
    checksum here fails in the quiet direction: it would simply never match,
    and a recogniser that finds nothing looks exactly like a jurisdiction
    with nothing to find.
    """
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) != 10 or digits[0] not in "01":
        return False
    return int(digits[8:]) == 97 - (int(digits[:8]) % 97)


class NLKvKRecognizer(PatternRecognizer):
    """Company registration number: NL KvK, or BE enterprise number.

    Two shapes with deliberately different gates, because the evidence
    available differs. The Dutch KvK number has no checksum at all, so
    nearby context words are the only thing separating it from an order
    number. The Belgian enterprise number has a modulo-97 check, which is
    stronger evidence than any context word, so it stands on its own -- the
    same split this pack already makes between NL_BSN (checksum) and
    NL_KVK (context).
    """

    PATTERNS = [
        Pattern("NL KvK (candidate)", r"(?<!\d)\d{8}(?!\d)", 0.3),
        Pattern("BE enterprise number (candidate)", r"(?<!\d)[01]\d{9}(?!\d)", 0.3),
        Pattern(
            "BE enterprise number, dotted (candidate)",
            r"(?<!\d)[01]\d{3}\.\d{3}\.\d{3}(?!\d)",
            0.3,
        ),
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
            matched = text[result.start : result.end]

            # A valid modulo-97 check is stronger evidence than any context
            # word, so the Belgian form does not need one. Checked before the
            # context gate rather than after: a BE number that happens to sit
            # next to "kvk" must not be waved through by the Dutch gate while
            # failing its own checksum.
            if _valid_be_enterprise_number(matched):
                result.score = EntityRecognizer.MAX_SCORE
                confirmed.append(result)
                continue

            # Only the Belgian patterns produce a 10-digit candidate, and one
            # that failed the checksum above is not a company number in either
            # jurisdiction -- NL KvK is 8 digits. Drop it rather than fall
            # through, or the context gate would promote any 10-digit order
            # number sitting near the word "kvk" to a full match.
            if sum(ch.isdigit() for ch in matched) != 8:
                continue

            window = text[
                max(0, result.start - _KVK_CONTEXT_WINDOW) : result.end
                + _KVK_CONTEXT_WINDOW
            ].lower()
            if any(word in window for word in _KVK_CONTEXT_WORDS):
                result.score = EntityRecognizer.MAX_SCORE
                confirmed.append(result)
        return confirmed


# ---------------------------------------------------------------------------
# NL_BTW — EU VAT identification number, all 27 member states
# ---------------------------------------------------------------------------
# The entity id stays `NL_BTW` deliberately. It is an internal key, it is
# CHECK-constrained in `portal_orgs.pii_masked_entities`, and the only
# customer-facing string for it already reads "VAT number" / "btw-nummer" --
# country-neutral. Renaming it would mean a data migration over live tenant
# rows plus a frontend union type, to change a label nobody sees. Recorded
# here rather than left to be rediscovered: the id says NL, the coverage is
# EU-wide.
#
# Formats per member state are taken from the VAT-identification-number
# reference (verified 2026-08-25), not from memory. The two traps the source
# calls out are both handled below: Greece uses EL rather than GR, and four
# countries accept more than one length (BG, CZ, LT, RO).
#
# Every prefix is wrapped in `(?-i:...)`. The registry applies IGNORECASE
# globally, and a bare `\bDE\d{9}\b` would therefore also match `de123456789`
# -- `de` being the most common word in Dutch. This is the same failure that
# made `2026 en` a postcode; the fix is the same, applied before it can bite
# rather than after.
_EU_VAT_PATTERNS: tuple[tuple[str, str], ...] = (
    # Prefix,                body
    ("AT", r"U\d{8}"),                    # 'U' + 8 digits
    ("BE", r"\d{10}"),                    # 8 digits + 2 check digits
    ("BG", r"\d{9,10}"),                  # 9 or 10
    ("CY", r"\d{8}[A-Z]"),                # 8 digits + 1 letter
    ("CZ", r"\d{8,10}"),                  # 8, 9 or 10
    ("DE", r"\d{9}"),
    ("DK", r"\d{8}"),
    ("EE", r"\d{9}"),
    ("EL", r"\d{9}"),                     # Greece is EL, never GR
    ("ES", r"[A-Z0-9]\d{7}[A-Z0-9]"),     # letter/digit + 7 digits + letter/digit
    ("FI", r"\d{8}"),
    ("FR", r"[A-Z0-9]{2}\d{9}"),          # 2 validation chars + 9-digit SIREN
    ("HR", r"\d{11}"),
    ("HU", r"\d{8}"),
    ("IE", r"\d{7}[A-Z]{1,2}"),           # 7 digits + 1 or 2 letters
    ("IT", r"\d{11}"),
    ("LT", r"(?:\d{12}|\d{9})"),          # 12 before 9: longest alternative first
    ("LU", r"\d{8}"),
    ("LV", r"\d{11}"),
    ("MT", r"\d{8}"),
    ("NL", r"\d{9}B\d{2}"),
    ("PL", r"\d{10}"),
    ("PT", r"\d{9}"),
    ("RO", r"\d{2,10}"),
    ("SE", r"\d{12}"),
    ("SI", r"\d{8}"),
    ("SK", r"\d{10}"),
)


def _eu_vat_regex() -> str:
    """One alternation over every member state, prefixes case-sensitive.

    Sorted longest-prefix-irrelevant (all prefixes are two characters) but
    each body is anchored by `\\b` on both sides, so `DE123456789` cannot
    be clipped out of a longer digit run.
    """
    branches = "|".join(f"(?-i:{code})(?:{body})" for code, body in _EU_VAT_PATTERNS)
    return rf"\b(?:{branches})\b"


class NLBTWRecognizer(PatternRecognizer):
    """EU VAT identification number. Format recognizer, no checksum.

    Was NL-only (`NL\\d{9}B\\d{2}`) until 2026-08-25. A tenant operating in
    Belgium or Germany had its VAT numbers reach the model unmasked even
    with the "VAT number" box ticked, because the pattern only ever matched
    the Dutch form -- the box was honest about intent and wrong about
    coverage.
    """

    PATTERNS = [
        Pattern("EU VAT", _eu_vat_regex(), 0.7),
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
# most common key format through unmasked. The body is base64
# (`A-Za-z0-9+/=`) plus whitespace, PLUS `:`, `,`, `-` for the two RFC-1421 /
# OpenSSL "traditional" encrypted-key header lines that can precede the
# base64 (`Proc-Type: 4,ENCRYPTED` / `DEK-Info: AES-256-CBC,<hex-IV>` — the
# cipher name itself contains a hyphen). `[A-Za-z0-9+/=:,\-\s]{0,_PEM_MAX_BODY_CHARS}?`
# lets the span run across newlines to the matching END marker, so key
# material is never left in the payload. A BEGIN with no following END never
# matches at all — "bare unmatched PEM header" (AC-5) stays undetected by
# design, since a half pattern is not sensitive key material.
#
# Bounded, not `.*?` (system-review finding M4). `.*?` + DOTALL is quadratic
# on a payload containing many unmatched BEGIN markers: from every BEGIN, the
# engine backtracks forward looking for an END that never comes, and does
# that scan again from the next BEGIN, and the next — O(n^2) in the number of
# markers. Measured on the unbounded pattern (deploy/presidio/tests/
# test_klai_pii_recognizers.py::TestSecretPemPerformance fixtures): 200
# markers ~10ms, 1000 ~257ms, 4000 (~340KB) ~4.1s on a single core — and
# presidio-analyzer runs with `cpus: '1'`, shared by every tenant, with no
# size cap upstream on what the Phase 2 observer or a future Phase 3
# enforcement call sends it. Bounding the body length turns "scan to the end
# of the payload" into "give up after `_PEM_MAX_BODY_CHARS` chars", which
# caps the backtracking cost per BEGIN marker instead of letting it grow with
# the remaining text length: same fixtures measured 200 markers ~7ms, 1000
# ~40ms, 4000 ~171ms — still growing roughly linearly with marker count, NOT
# quadratically, and a 25x improvement over the unbounded pattern at 4000
# markers. This is higher than a base64-only character class (~0.1/0.7/2.7ms
# at the same marker counts — an earlier version of this fix used that
# narrower class) because `-` now overlaps with the marker text itself
# (`-----BEGIN...`), giving the engine more candidate body-continuations to
# try within the same bounded window before giving up — a real, measured
# trade-off, made deliberately in favour of not missing a real credential
# (a caught-by-review regression: the base64-only class could no longer
# detect a legacy encrypted PEM key, e.g. `openssl rsa -aes256 -traditional`
# output, at all — see TestSecretPemBounded's encrypted-key test). Both
# upstream length-cap fixes in `klai_pii_observe.py` /
# `klai_pii_enforce.py` mean this regex now never actually sees more than
# `_MAX_ANALYZE_CHARS` (20,000) characters in one `/analyze` call regardless
# of the original payload size: an adversarial 20,000-char payload packed
# with unmatched BEGIN markers measures ~8ms end to end with this pattern —
# comfortably inside the 60ms NFR budget for the whole analysis pipeline.
#
# 5000 chars covers every realistic PEM private key body: a real PKCS#8 RSA
# 4096-bit key (the largest RSA size in common use) base64-encodes to ~3.2K
# characters (measured directly via `cryptography.hazmat` — 2048-bit is
# ~1.6K, EC/Ed25519 keys are much shorter). 5000 leaves comfortable headroom
# above the largest key anyone will plausibly paste while still bounding the
# worst-case scan per BEGIN marker. A key body genuinely longer than that is
# not a realistic PEM private key and this recognizer intentionally does not
# chase it.
_PEM_MAX_BODY_CHARS = 5000

_SECRET_PATTERNS = [
    Pattern(
        "PEM private key block",
        r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----"
        rf"[A-Za-z0-9+/=:,\-\s]{{0,{_PEM_MAX_BODY_CHARS}}}?"
        r"-----END(?: [A-Z0-9]+)? PRIVATE KEY-----",
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
    """PhoneRecognizer covering the EU/EEA, not just NL and BE.

    Widened on 2026-08-25. NL and BE plus the stock regions left the rest of
    the single market uncovered: a French, Spanish, Italian or Portuguese
    number reached the model in full with the "phone number" box ticked.

    Adding a region is close to free here, unlike adding a regex. The
    underlying `phonenumbers` library validates against each region's real
    numbering plan rather than a digit-count pattern, so a region that does
    not apply to a tenant contributes no false positives -- the same
    "a recogniser that finds nothing costs nothing" argument REQ-2 already
    makes for jurisdiction-specific checksums.
    """

    KLAI_EU_EEA_REGIONS = (
        "NL", "BE", "LU", "FR", "ES", "PT", "IT", "AT", "IE", "DK", "SE",
        "FI", "PL", "CZ", "SK", "HU", "RO", "BG", "HR", "SI", "EE", "LV",
        "LT", "GR", "CY", "MT", "NO", "IS", "LI", "CH",
    )

    KLAI_SUPPORTED_REGIONS = KLAI_EU_EEA_REGIONS + PhoneRecognizer.DEFAULT_SUPPORTED_REGIONS

    def __init__(self, **kwargs):
        kwargs.setdefault("supported_regions", self.KLAI_SUPPORTED_REGIONS)
        super().__init__(**kwargs)
