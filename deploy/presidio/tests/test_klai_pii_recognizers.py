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


# ---------------------------------------------------------------------------
# System-review finding M4 — PEM pattern is quadratic on unmatched BEGIN
# markers. Fixed by bounding the body to `_PEM_MAX_BODY_CHARS` instead of an
# unbounded `.*?`. Two things must both hold: a real key still matches in
# full, and the pathological-input cost is actually bounded now.
# ---------------------------------------------------------------------------

# A real legacy-encrypted RSA-2048 key (RFC 1421 / OpenSSL "traditional"
# format: `Proc-Type` / `DEK-Info` header lines before the base64 body),
# generated once with `openssl rsa -aes256 -traditional` on a throwaway key
# and pasted in literally (test-only key, passphrase not reused anywhere).
_LEGACY_ENCRYPTED_RSA_PEM = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "Proc-Type: 4,ENCRYPTED\n"
    "DEK-Info: AES-256-CBC,74872D43B0A9C5BF71826452B86B338F\n"
    "\n"
    "x8KyAcN+kZPB/X7GVmxGDQjYOF8R1pj48B1FvedXCCPrtXAQC9dHREacE8YH/JVk\n"
    "o6z8BcM84mZiIZgwhrNuzAK10Vr2fMdkMcGwjateDwHcv3RRzBqhBXIa2AXasDc3\n"
    "3uRaKaqXAm7HGM1FKkpaWekQwlk0bdAwEBzvhX0fplSNeWaPALyKd6sYy41GUQbx\n"
    "sORJ2Hb/U8YfHb5MsksdRsz1V81cfAOaVNNIQHpUVeJd4ZgsrrlK9+I3YThXgtIK\n"
    "LruLRctQ+ijLpnQIvmIe858KfVZTxBOcJkOb6ii8ryKJ8zBFqfi5kuQ2/R5t3rpC\n"
    "nwnQ2gHXy708uw7mQWu1WD/HZQGsZB5n9I8rwOWevENMb0hHILPLrpHdKPAWCAZP\n"
    "JAGfuxLx+6DsF65qcRJr80jPlKHb0SEb9VnHvvhZJ6Wllc9QqMb2gxQIgdKN3yyb\n"
    "O5o14hAHvZ/guRtFq1Ydi2ZJsBskwgn1YoWIRJbf5VHaKPnyzqp6b7vsiM2gu8+G\n"
    "RZQFqeD/sTHNMrvYmuYhV1FgZ/E8vc16NuHeAnGHIdan5BBDuBs3bsXI52jBAlW1\n"
    "PZTfQ/wNyotJ6LRvHd+2ts+pItuNE7cIhpnkRZjNtJMACOv2qjdAm8h4BtSGFboi\n"
    "MEnnTjPpi6XyGLZOjk7swaC33414zXhz5Cth75+OUpl1fZ9bsH8EM1+tFYgMnH5t\n"
    "qoIifNQQBJj3wh4Ods+JWC4CKtGsGSzr9f2jJ4zha3VPp98VZew7UADw1jpFjq+P\n"
    "FxoQNj9JsMBEOPU/CPhRd+KmCHXj8+Rxojad5jZ6Dwy94i0HraI1Um1lpk6Pyfsh\n"
    "g2ncZamkBTOyZtgWP1ubbvSyRLRQtiI12OJPWFFEqz9pOJOFDt5b6nyuSjgK6iJ7\n"
    "ELlXCgEWkFr7fxx6utfvoatV9xa4s/4tby8BdR1lyP5EDqXy334GgmjMHbyDttVx\n"
    "ocbuDrBb1K6LTbIaO9uT9OXers/gCj2X+Eg9w3eMOUB8esSEgMbTxae3lBtAI7i5\n"
    "5e7To1cSpCs1NS/2+aM7zBZbN1oUAU0ufD75HUIQfRe3cLxQwlKPyVSI+mkHhs0b\n"
    "J91mQiWxne8lL7dhtnSPKxHnWgkBfXFLOxmPckhqB8tBQsLfPljV8Og3dg/7bn0g\n"
    "OoflCtqhNIa9lT/g+E0bXVT+3lVRFBfTbCfl4gLCjg4q7Fd9L9Her9dfG4rvWpqR\n"
    "Bm6tJoAcanjz492Jq+RZT6BL4XvJWgzOslG+N8kd0V38jfAxZzYUnh3a9sM6QbRh\n"
    "eHT6R6wFF4n64DArWOH48Dg77PmPgUOYqKJLsL8u9dI9BcLXFsU3d2k1pJHrzUco\n"
    "vKgNDvKh7CwKqcpQBgQFJMx9QpqoNIL2ORjOFNIQNyjVdFeZqF64ZPyHJu++B3IB\n"
    "OCDTgrXO+SfAVos8gbWku4Esv1ORwSQRcYHhl0Rc87XvTD8OMTBFhI0dk8jyGyoy\n"
    "6Otn6RyFnDlELOnWNqRhVJI5aH0QNTjsZxQdS3k5N01MPehOxTv56AbqqN2aZR3d\n"
    "qmYxWJKDpa4aZmodYINvObD5U5VGADwctlUzOxH0I7eaV0p4yPOgwCGg07GCdRmz\n"
    "-----END RSA PRIVATE KEY-----"
)

# A real PKCS#8 RSA-4096 private key body (the largest RSA size in common
# use), generated once with `cryptography.hazmat` and pasted in literally so
# this test needs no crypto library at run time. 4096-bit is the size most
# likely to approach `_PEM_MAX_BODY_CHARS` — smaller RSA sizes and EC/Ed25519
# keys are all shorter.
_REAL_RSA4096_PKCS8 = (
    "-----BEGIN PRIVATE KEY-----\n"
    "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\n"
    "7Nuq0kIyHfue9xrq/FyLF/0ZZv6GSQ==\n"
    "-----END PRIVATE KEY-----"
)


class TestSecretPemBounded:
    """System-review finding M4: the PEM pattern was `.*?` + DOTALL, which is
    quadratic on a payload containing many unmatched BEGIN markers. Bounding
    the body to `_PEM_MAX_BODY_CHARS` must not break real-key detection.
    """

    def test_real_rsa4096_pkcs8_key_still_fully_detected(self):
        rec = SecretRecognizer()
        text = f"Here is the key:\n{_REAL_RSA4096_PKCS8}\ndone"
        results = _detected(rec, text, "SECRET")
        pem_results = [r for r in results if text[r.start : r.end].startswith("-----BEGIN")]
        assert len(pem_results) == 1
        matched = text[pem_results[0].start : pem_results[0].end]
        assert matched == _REAL_RSA4096_PKCS8
        assert "done" not in matched

    def test_legacy_encrypted_pem_with_proc_type_header_still_detected(self):
        """Sol-review finding: the first version of this fix's character
        class was base64-only (`A-Za-z0-9+/=`) and could no longer detect
        the RFC 1421 / OpenSSL "traditional" encrypted-key format, which
        has two metadata lines BEFORE the base64 body:

            -----BEGIN RSA PRIVATE KEY-----
            Proc-Type: 4,ENCRYPTED
            DEK-Info: AES-256-CBC,<hex-IV>

            <base64 body>
            -----END RSA PRIVATE KEY-----

        `Proc-Type: 4,ENCRYPTED` and `DEK-Info: AES-256-CBC,...` contain
        `:`, `,` and `-` (the cipher name itself is hyphenated), none of
        which were in the base64-only class. A real key generated this way
        (`openssl rsa -aes256 -traditional`) would have gone through
        unmasked under enforcement. Regression test for the fix that added
        `:,\\-` to the body character class.
        """
        rec = SecretRecognizer()
        text = f"Hier is de sleutel:\n{_LEGACY_ENCRYPTED_RSA_PEM}\nklaar"
        results = _detected(rec, text, "SECRET")
        pem_results = [r for r in results if text[r.start : r.end].startswith("-----BEGIN")]
        assert len(pem_results) == 1
        matched = text[pem_results[0].start : pem_results[0].end]
        assert matched == _LEGACY_ENCRYPTED_RSA_PEM
        assert "Proc-Type" in matched
        assert "DEK-Info" in matched
        assert "klaar" not in matched

    def test_body_longer_than_bound_is_a_documented_non_match(self):
        """Deliberate design boundary, not a bug: a body past
        `_PEM_MAX_BODY_CHARS` is not chased. No real PEM private key body
        (largest measured: RSA-4096 at ~3.2K chars) gets anywhere near this."""
        from klai_pii_recognizers import _PEM_MAX_BODY_CHARS

        oversized_body = "A" * (_PEM_MAX_BODY_CHARS + 1)
        text = f"-----BEGIN PRIVATE KEY-----\n{oversized_body}\n-----END PRIVATE KEY-----"
        rec = SecretRecognizer()
        results = _detected(rec, text, "SECRET")
        pem_results = [r for r in results if "BEGIN PRIVATE KEY" in text[r.start : r.end]]
        assert pem_results == []


class TestSecretPemPerformance:
    """Regression for system-review finding M4's actual measurement.

    presidio-analyzer runs with `cpus: '1'`, shared across every tenant, and
    nothing upstream caps payload size before this recognizer runs (the
    length caps in klai_pii_observe.py / klai_pii_enforce.py are a separate,
    second layer of defense — this test is about the regex alone). The old
    `.*?` pattern took ~4.1s on a single core for 4000 unmatched BEGIN
    markers (~340KB) — a chat request from one tenant pasting a payload
    shaped like this would peg the shared analyzer for every other tenant at
    once. The bounded pattern must stay comfortably sub-second at the same
    marker counts: measured at ~7ms / ~40ms / ~171ms for 200 / 1000 / 4000
    markers (the character class also carries `:,-` now, for the legacy
    encrypted-PEM fix below — that raises the constant factor versus a
    base64-only class, ~0.1/0.7/2.7ms, but the growth stays roughly linear in
    marker count either way, not quadratic). Generous bound (2s) to avoid
    CI-runner flakiness while still failing hard if the quadratic behaviour
    ever comes back — the old pattern blew well past this bound even at 1000
    markers (~257ms) and catastrophically at 4000 (~4.1s).
    """

    @staticmethod
    def _payload_with_unmatched_begin_markers(n_markers: int, filler_len: int = 60) -> str:
        filler = "A" * filler_len + "\n"
        return "".join(f"-----BEGIN PRIVATE KEY-----\n{filler}" for _ in range(n_markers))

    @pytest.mark.parametrize("n_markers", [200, 1000, 4000])
    def test_unmatched_begin_markers_do_not_blow_up(self, n_markers):
        import time

        rec = SecretRecognizer()
        payload = self._payload_with_unmatched_begin_markers(n_markers)

        start = time.perf_counter()
        results = _detected(rec, payload, "SECRET")
        elapsed = time.perf_counter() - start

        assert elapsed < 2.0, f"{n_markers} unmatched BEGIN markers took {elapsed:.3f}s"
        # None of these should register as a PEM match -- no END marker
        # anywhere in the payload.
        pem_results = [r for r in results if "BEGIN PRIVATE KEY" in payload[r.start : r.end]]
        assert pem_results == []
