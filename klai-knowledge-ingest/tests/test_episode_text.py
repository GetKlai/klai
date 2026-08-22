from unittest.mock import patch

import pytest

from knowledge_ingest.episode_text import split_episode_text


def test_split_episode_text_preserves_paragraph_and_sentence_boundaries():
    text = "Alpha sentence.\n\nBravo sentence.\n\nCharlie sentence."

    parts = split_episode_text(text, max_chars=34)

    assert parts == ["Alpha sentence.\n\nBravo sentence.", "Charlie sentence."]
    assert "\n\n".join(parts) == text
    assert all(part.endswith(".") for part in parts)
    assert all(len(part) <= 34 for part in parts)


@pytest.mark.parametrize(
    ("text", "max_chars", "expected_first_part"),
    [
        ("first\nsecond line", 10, "first"),
        ("alpha beta gamma", 12, "alpha beta "),
        ("x" * 25, 10, "x" * 10),
    ],
)
def test_oversized_paragraph_uses_fallback_boundaries_without_dropping_text(
    text, max_chars, expected_first_part
):
    with patch("knowledge_ingest.episode_text.logger", create=True) as logger:
        parts = split_episode_text(text, max_chars=max_chars)

    assert "".join(parts) == text
    assert parts[0] == expected_first_part
    assert all(0 < len(part) <= max_chars for part in parts)
    logger.warning.assert_called_once()


def test_over_cap_document_starting_with_blank_line_has_no_empty_episode():
    text = "\n\n" + "x" * 25

    parts = split_episode_text(text, max_chars=25)

    assert parts
    assert "".join(parts) == text
    assert all(part.strip() for part in parts)
