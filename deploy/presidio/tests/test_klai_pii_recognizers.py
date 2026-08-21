"""Unit tests for the Klai Presidio recognizer pack (SPEC-PRIVACY-MISTRAL-PII-001
Phase 1, AC-3 through AC-6, and the REQ-2 language-agnosticism guarantee).

No network, no Docker, no NLP engine: every recognizer here is regex-plus-
checksum and is exercised directly via `.analyze(text, entities)` on a bare
recognizer instance — the same code path Presidio's own AnalyzerEngine calls,
just without the AnalyzerEngine/NlpEngine machinery around it (which is what
`test_analyzer_registry_config.py` covers end to end, including spaCy).
"""

from __future__ import annotations

import conftest  # noqa: F401  (adds ../analyzer to sys.path)
import pytest
from presidio_analyzer.predefined_recognizers import CreditCardRecognizer, IbanRecognizer

from klai_pii_recognizers import (
    NLBSNRecognizer,
    NLBTWRecognizer,
    NLKvKRecognizer,
    NLPostcodeRecognizer,
    SecretRecognizer,
)


def _detected(recognizer, text, entity):
    return recognizer.analyze(text, [entity])


# ---------------------------------------------------------------------------
# AC-3 — NL_BSN: valid elfproef detected, invalid not detected
# ---------------------------------------------------------------------------


class TestNLBSN:
    def test_valid_bsn_detected(self):
        rec = NLBSNRecognizer()
        results = _detected(rec, "Mijn BSN is 111222333.", "NL_BSN")
        assert len(results) == 1
        assert results[0].entity_type == "NL_BSN"
        assert results[0].score == 1.0

    def test_elfproef_failure_not_detected(self):
        rec = NLBSNRecognizer()
        # 111222333 passes; 111222334 (last digit changed) fails the checksum.
        results = _detected(rec, "Mijn BSN is 111222334.", "NL_BSN")
        assert results == []

    def test_ported_checksum_matches_shield_compliance(self):
        # Cross-check against klai-portal/backend's own `_valid_bsn` behaviour
        # (SPEC-SHIELD-001) so drift between the two copies is caught here,
        # not in production. 8-digit form gets left-padded with a zero.
        from klai_pii_recognizers import _valid_bsn

        assert _valid_bsn("111222333") is True
        assert _valid_bsn("12345672") is True  # 8-digit form, left-padded to 012345672
        assert _valid_bsn("111222334") is False
        assert _valid_bsn("12345678") is False  # not a valid BSN


# ---------------------------------------------------------------------------
# AC-4 — IBAN_CODE (built-in, mod-97): grouped valid IBAN detected, invalid not
# ---------------------------------------------------------------------------


class TestIBAN:
    def test_valid_grouped_iban_detected(self):
        rec = IbanRecognizer()
        results = _detected(rec, "Mijn IBAN is NL91 ABNA 0417 1643 00", "IBAN_CODE")
        assert len(results) == 1
        assert results[0].score == 1.0

    def test_mod97_invalid_iban_not_detected(self):
        rec = IbanRecognizer()
        # Last check digit changed (00 -> 01): fails the mod-97 checksum.
        results = _detected(rec, "Mijn IBAN is NL91 ABNA 0417 1643 01", "IBAN_CODE")
        assert results == []


# ---------------------------------------------------------------------------
# Luhn-valid / Luhn-invalid credit card (built-in)
# ---------------------------------------------------------------------------


class TestCreditCard:
    def test_luhn_valid_detected(self):
        rec = CreditCardRecognizer()
        results = _detected(rec, "Card: 4111 1111 1111 1111", "CREDIT_CARD")
        assert len(results) == 1
        assert results[0].score == 1.0

    def test_luhn_invalid_not_detected(self):
        rec = CreditCardRecognizer()
        results = _detected(rec, "Card: 4111 1111 1111 1112", "CREDIT_CARD")
        assert results == []


# ---------------------------------------------------------------------------
# AC-5 — SECRET: PEM (no type token) spans to END; bare header undetected;
# JWT, Bearer, provider-key prefix.
# ---------------------------------------------------------------------------

_PEM_NO_TYPE = (
    "Here is the key:\n"
    "-----BEGIN PRIVATE KEY-----\n"
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj\n"
    "MzEfYyjiWA4R4/M2bS1GB4t7NXp98C3SC6dVMvDuictGeurT8jNbvJZHtCSuYEvu\n"
    "-----END PRIVATE KEY-----\n"
    "done"
)


class TestSecret:
    def test_pem_no_type_token_detected_span_reaches_end_marker(self):
        rec = SecretRecognizer()
        results = _detected(rec, _PEM_NO_TYPE, "SECRET")
        pem_results = [r for r in results if _PEM_NO_TYPE[r.start : r.end].startswith("-----BEGIN")]
        assert len(pem_results) == 1
        matched = _PEM_NO_TYPE[pem_results[0].start : pem_results[0].end]
        assert matched.startswith("-----BEGIN PRIVATE KEY-----")
        assert matched.endswith("-----END PRIVATE KEY-----")
        assert "done" not in matched  # span stops at the marker, not beyond
        assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj" in matched

    def test_bare_unmatched_pem_header_not_detected(self):
        rec = SecretRecognizer()
        text = "header only: -----BEGIN PRIVATE KEY----- and nothing else"
        results = _detected(rec, text, "SECRET")
        pem_results = [r for r in results if "BEGIN PRIVATE KEY" in text[r.start : r.end]]
        assert pem_results == []

    def test_jwt_detected(self):
        rec = SecretRecognizer()
        jwt = (
            "token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        results = _detected(rec, jwt, "SECRET")
        assert any(jwt[r.start : r.end].startswith("eyJ") for r in results)

    def test_bearer_header_detected(self):
        rec = SecretRecognizer()
        text = "Authorization: Bearer abcdEFGH12345678ijklMNOPqrst"
        results = _detected(rec, text, "SECRET")
        assert any("Bearer" in text[r.start : r.end] for r in results)

    def test_provider_key_prefix_detected(self):
        rec = SecretRecognizer()
        for text, prefix in [
            ("token: sk-abcdEFGH12345678ijklMNOPqrst", "sk-"),
            ("token: ghp_1234567890abcdefghijklmnopqrstuv", "ghp_"),
            # Assembled rather than written literally: a literal Slack-shaped
            # token trips GitHub push protection on every push, even inside a
            # fixture for the detector that is supposed to catch it. The
            # recognizer receives an identical string either way.
            ("token: " + "xoxb" + "-1234567890-abcdefghijklmnop", "xoxb-"),
            # Current, hyphen-segmented provider key shapes — a bare
            # `sk-[A-Za-z0-9]{16,}` pattern (no hyphen in the body) misses
            # both: neither has 16 alnum characters before its first
            # internal hyphen. Regression for a sol-review finding.
            ("token: sk-ant-api03-abcdefghijklmnopqrstuvwxyzABCDEFG", "sk-ant-"),
            ("token: sk-proj-abcdefghijklmnopqrstuvwxyz1234567890", "sk-proj-"),
        ]:
            results = _detected(rec, text, "SECRET")
            assert any(prefix in text[r.start : r.end] for r in results), text

    def test_bearer_token_with_base64_special_chars_detected(self):
        # RFC 6750 b64token alphabet includes "+", "/", "~" in addition to
        # "-", "_", ".": a Bearer pattern that excludes them misses real
        # base64(url) tokens. Regression for a sol-review finding.
        rec = SecretRecognizer()
        text = "Authorization: Bearer abc+DEF/ghi~JKL1234567890=="
        results = _detected(rec, text, "SECRET")
        assert any("Bearer" in text[r.start : r.end] for r in results)


# ---------------------------------------------------------------------------
# AC-6 — NL_KVK, NL_BTW, NL_POSTCODE each detected
# ---------------------------------------------------------------------------


class TestKvK:
    def test_kvk_with_context_detected(self):
        rec = NLKvKRecognizer()
        results = _detected(rec, "KvK-nummer: 12345678", "NL_KVK")
        assert len(results) == 1
        assert results[0].score == 1.0

    def test_kvk_without_context_not_detected(self):
        rec = NLKvKRecognizer()
        results = _detected(rec, "Bestelnummer: 12345678", "NL_KVK")
        assert results == []

    def test_kvk_context_via_handelsregister_word(self):
        rec = NLKvKRecognizer()
        results = _detected(
            rec, "Ingeschreven in het handelsregister onder 87654321.", "NL_KVK"
        )
        assert len(results) == 1


class TestBTW:
    def test_btw_detected(self):
        rec = NLBTWRecognizer()
        results = _detected(rec, "BTW-nummer: NL123456789B01", "NL_BTW")
        assert len(results) == 1


class TestPostcode:
    def test_postcode_detected(self):
        rec = NLPostcodeRecognizer()
        results = _detected(rec, "Adres: Hoofdstraat 1, 1234 AB Amsterdam", "NL_POSTCODE")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# REQ-2 — language-agnosticism: the same BSN / IBAN / credential embedded in
# an English, Dutch, and third-language sentence is detected identically.
# This is the actual guarantee REQ-2 makes: none of these recognizers read
# `supported_language` as a correctness input — only as a registry label.
# ---------------------------------------------------------------------------

_BSN_VALUE = "111222333"
_SENTENCES = {
    "en": f"Please note my BSN is {_BSN_VALUE} for the application.",
    "nl": f"Let op, mijn BSN is {_BSN_VALUE} voor de aanvraag.",
    "de": f"Bitte beachten Sie, meine BSN ist {_BSN_VALUE} für den Antrag.",
}

_SECRET_VALUE = "sk-abcdEFGH12345678ijklMNOPqrst"
_SECRET_SENTENCES = {
    "en": f"Use this API key: {_SECRET_VALUE} in the request header.",
    "nl": f"Gebruik deze API-sleutel: {_SECRET_VALUE} in de request header.",
    "de": f"Verwenden Sie diesen API-Schlüssel: {_SECRET_VALUE} im Anfrage-Header.",
}


class TestLanguageAgnosticism:
    @pytest.mark.parametrize("language", ["en", "nl", "de"])
    def test_bsn_detected_identically_across_languages(self, language):
        rec = NLBSNRecognizer(supported_language=language)
        text = _SENTENCES[language]
        results = rec.analyze(text, ["NL_BSN"])
        assert len(results) == 1
        assert text[results[0].start : results[0].end] == _BSN_VALUE
        assert results[0].score == 1.0

    def test_bsn_detection_is_independent_of_supported_language_value(self):
        # The recognizer's own regex/checksum logic must not branch on
        # `supported_language` at all — feeding the SAME text through
        # instances configured for different languages must yield the exact
        # same span and score.
        text = f"BSN: {_BSN_VALUE}"
        results = {
            lang: NLBSNRecognizer(supported_language=lang).analyze(text, ["NL_BSN"])
            for lang in ("en", "nl", "de", "fr", "es")
        }
        spans = {lang: [(r.start, r.end, r.score) for r in res] for lang, res in results.items()}
        assert len(set(tuple(v) for v in spans.values())) == 1, spans

    @pytest.mark.parametrize("language", ["en", "nl", "de"])
    def test_secret_detected_identically_across_languages(self, language):
        rec = SecretRecognizer(supported_language=language)
        text = _SECRET_SENTENCES[language]
        results = rec.analyze(text, ["SECRET"])
        matched = [text[r.start : r.end] for r in results]
        assert any(_SECRET_VALUE in m for m in matched), (language, matched)

    def test_iban_detection_is_independent_of_supported_language_value(self):
        iban = "NL91 ABNA 0417 1643 00"
        text = f"IBAN: {iban}"
        by_lang = {}
        for lang in ("en", "nl", "de"):
            rec = IbanRecognizer(supported_language=lang)
            results = rec.analyze(text, ["IBAN_CODE"])
            by_lang[lang] = [(r.start, r.end, r.score) for r in results]
        assert len(set(tuple(v) for v in by_lang.values())) == 1, by_lang


# ---------------------------------------------------------------------------
# NL_PHONE — the region list must actually reach the recognizer
# ---------------------------------------------------------------------------
class TestNLPhoneRecognizer:
    """Regression for a LIVE bug found by introspecting the running container.

    `supported_regions:` on a `type: predefined` YAML entry is silently
    dropped by Presidio's registry loader, so the deployed PhoneRecognizer ran
    with DEFAULT_SUPPORTED_REGIONS — which has no NL. Dutch numbers were being
    detected only because most of them also parse as valid German ones;
    Rotterdam's 010 range has no German equivalent and was never detected in
    any format.
    """

    def test_nl_and_be_are_in_the_region_list(self):
        from klai_pii_recognizers import NLPhoneRecognizer

        rec = NLPhoneRecognizer()
        assert "NL" in rec.supported_regions
        assert "BE" in rec.supported_regions

    def test_stock_regions_are_retained_not_replaced(self):
        """Narrowing to NL alone would trade one detection gap for another —
        a tenant's documents legitimately contain foreign numbers."""
        from presidio_analyzer.predefined_recognizers import PhoneRecognizer

        from klai_pii_recognizers import NLPhoneRecognizer

        rec = NLPhoneRecognizer()
        for region in PhoneRecognizer.DEFAULT_SUPPORTED_REGIONS:
            assert region in rec.supported_regions, region

    @pytest.mark.parametrize(
        "number",
        [
            "010-7654321",   # Rotterdam — the format that was never detected
            "010-2345678",
            "0107654321",
            "010 7654321",
            "020-1234567",
            "06-12345678",
            "+31 6 12345678",
        ],
    )
    def test_dutch_numbers_detected(self, number):
        from klai_pii_recognizers import NLPhoneRecognizer

        rec = NLPhoneRecognizer()
        text = f"Bel mij op {number} alsjeblieft."
        results = rec.analyze(text, entities=["PHONE_NUMBER"], nlp_artifacts=None)
        assert results, f"{number} not detected"


# ---------------------------------------------------------------------------
# System-review fixes (2026-08-20) — irreversible false positives
# ---------------------------------------------------------------------------
class TestIrreversibleFalsePositives:
    """NL_BSN and SECRET are masked for every org and NEVER restored, so a
    false positive here silently destroys user text with no way back. These
    three patterns each did exactly that before the system review."""

    @pytest.mark.parametrize(
        "date_digits", ["20200109", "20200110", "20200122", "20200201", "20200213"]
    )
    def test_yyyymmdd_dates_are_not_bsn_without_context(self, date_digits):
        """~9% of YYYYMMDD dates pass the padded elfproef (365 of 4018 over
        2020-2030). Nine digits stands on the checksum; eight needs context."""
        from klai_pii_recognizers import NLBSNRecognizer

        rec = NLBSNRecognizer()
        text = f"Factuurdatum {date_digits} op de nota."
        assert rec.analyze(text, entities=["NL_BSN"], nlp_artifacts=None) == []

    def test_eight_digit_bsn_still_detected_with_context(self):
        from klai_pii_recognizers import NLBSNRecognizer

        rec = NLBSNRecognizer()
        text = "Mijn bsn is 10000008 graag verwerken."
        assert rec.analyze(text, entities=["NL_BSN"], nlp_artifacts=None)

    def test_nine_digit_bsn_needs_no_context(self):
        from klai_pii_recognizers import NLBSNRecognizer

        rec = NLBSNRecognizer()
        assert rec.analyze(
            "Het nummer 111222333 hoort erbij.", entities=["NL_BSN"], nlp_artifacts=None
        )

    def test_bearer_in_prose_is_not_a_secret(self):
        """Unanchored + registry IGNORECASE matched two words of Dutch prose."""
        from klai_pii_recognizers import SecretRecognizer

        rec = SecretRecognizer()
        text = "De bearer verantwoordelijkheid ligt bij de klant."
        assert rec.analyze(text, entities=["SECRET"], nlp_artifacts=None) == []

    def test_authorization_bearer_header_still_detected(self):
        from klai_pii_recognizers import SecretRecognizer

        rec = SecretRecognizer()
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abcdefghij"
        assert rec.analyze(text, entities=["SECRET"], nlp_artifacts=None)

    def test_year_plus_dutch_word_is_not_a_postcode(self):
        """Registry IGNORECASE turned [A-Z]{2} into 'any two letters', so
        '2026 en' matched. Masking it hides the year from the model."""
        from klai_pii_recognizers import NLPostcodeRecognizer

        rec = NLPostcodeRecognizer()
        text = "In 2026 en 2027 gaan we door."
        assert rec.analyze(text, entities=["NL_POSTCODE"], nlp_artifacts=None) == []

    def test_real_postcode_still_detected(self):
        from klai_pii_recognizers import NLPostcodeRecognizer

        rec = NLPostcodeRecognizer()
        assert rec.analyze(
            "Zie ook 3011 AB Rotterdam.", entities=["NL_POSTCODE"], nlp_artifacts=None
        )
