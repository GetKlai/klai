"""Tests for knowledge_ingest.fingerprint — SPEC-CRAWL-004 REQ-3/6."""

from knowledge_ingest.fingerprint import compute_content_fingerprint


def test_returns_16_char_hex() -> None:
    """Standard text returns a 16-character hex string."""
    text = " ".join(f"word{i}" for i in range(50))
    result = compute_content_fingerprint(text)
    assert len(result) == 16
    assert all(c in "0123456789abcdef" for c in result)


def test_short_input_returns_empty() -> None:
    """<20 words returns empty string."""
    assert compute_content_fingerprint("too short") == ""


def test_empty_input_returns_empty() -> None:
    assert compute_content_fingerprint("") == ""


def test_deterministic() -> None:
    """Same input produces same fingerprint."""
    text = "This is a test document with enough words for a meaningful fingerprint computation result"
    assert compute_content_fingerprint(text) == compute_content_fingerprint(text)


def test_compatible_with_connector() -> None:
    """Must produce identical output to klai-connector's trafilatura-based implementation.

    The test string and expected fingerprint are verified against the production
    connector container. If this test breaks, the two services produce incompatible
    fingerprints and canary checks will false-positive.
    """
    text = (
        "Customer Relationship Management software helps businesses manage "
        "interactions with current and potential customers tracking sales deals "
        "for improved customer service and automated marketing tasks"
    )
    result = compute_content_fingerprint(text)
    # This value was captured from klai-connector production (commit 73a89769):
    #   docker exec klai-core-klai-connector-1 python -c \
    #     'from app.services.content_fingerprint import compute_content_fingerprint; \
    #      print(compute_content_fingerprint("..."))'
    assert result == "e15fdadb7c8fa1af", f"Incompatible with connector: {result!r}"
