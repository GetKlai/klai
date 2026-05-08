"""SPEC-PRIVACY-QUERY-SHADOW-001 Unit 3 — features extraction unit tests.

Pure unit tests for the symbolic-feature extractor. No DB / network / pool.
"""

from __future__ import annotations

from retrieval_api.services.features import extract_features


def test_extract_features_empty_query_returns_zeroed_dict() -> None:
    feats = extract_features("")
    assert feats["tokens"] == 0
    assert feats["lang"] == "other"
    assert feats["has_brand"] is False
    assert feats["brand_count"] == 0
    assert feats["question_word"] is False
    assert feats["has_url"] is False
    assert feats["has_email_pattern"] is False


def test_extract_features_dutch_question() -> None:
    feats = extract_features("Hoe stel ik vakantie aan in het systeem?")
    assert feats["tokens"] >= 6
    assert feats["lang"] == "nl"
    assert feats["question_word"] is True
    assert feats["has_brand"] is False
    assert feats["brand_count"] == 0


def test_extract_features_english_question() -> None:
    feats = extract_features("How do I configure the connector?")
    assert feats["lang"] == "en"
    assert feats["question_word"] is True


def test_extract_features_brand_count_multiple() -> None:
    feats = extract_features("How do I sync Salesforce with Notion?")
    assert feats["has_brand"] is True
    assert feats["brand_count"] == 2


def test_extract_features_url_detected() -> None:
    feats = extract_features("see https://example.com/docs for details")
    assert feats["has_url"] is True


def test_extract_features_email_pattern_detected() -> None:
    feats = extract_features("contact info@klai.io for help")
    assert feats["has_email_pattern"] is True
    # The email also matches the brand 'klai' once tokenized — that's fine
    # because the feature is "did a brand appear", not "exactly one brand".
    assert feats["has_brand"] is True


def test_extract_features_does_not_leak_raw_text() -> None:
    """Defense-in-depth: features dict MUST NOT contain a 'query' or
    similar text-shaped value that could leak the original query."""
    secret = "S3CR3T-CUSTOMER-DATA-AND-SOCIAL-SECURITY"
    feats = extract_features(f"What about {secret}?")
    serialized = repr(feats)
    assert secret not in serialized
    # Sanity: the features dict only carries primitive bool/int/str-tags.
    for key, value in feats.items():
        assert isinstance(value, (bool, int, str)), f"unexpected type for {key}: {type(value)}"


def test_extract_features_other_language() -> None:
    feats = extract_features("Bonjour le monde")
    assert feats["lang"] == "other"
