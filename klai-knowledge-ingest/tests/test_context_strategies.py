"""Tests for knowledge_ingest/context_strategies.py"""
from knowledge_ingest.context_strategies import (
    STRATEGIES,
    extract_first_n_tokens,
    extract_front_matter,
    extract_most_recent_messages,
    extract_rolling_window,
)


def test_extract_first_n_tokens_truncates():
    # n=2 tokens -> ~8 chars
    result = extract_first_n_tokens("hello world this is a long text", 2)
    assert result == "hello wo"
    assert len(result) == 8


def test_extract_first_n_tokens_short_doc():
    result = extract_first_n_tokens("hi", 100)
    assert result == "hi"


def test_extract_rolling_window_chunk_index_0():
    doc = " ".join(f"word{i}" for i in range(200))
    result = extract_rolling_window(doc, 100, chunk_index=0)
    # chunk_index=0 -> center=0, so window starts from beginning
    assert result.startswith("word0")
    assert len(result) > 0


def test_extract_rolling_window_empty_doc():
    result = extract_rolling_window("", 100, chunk_index=0)
    assert result == ""


def test_extract_most_recent_messages_returns_end():
    doc = "a" * 100
    # n=10 -> 40 chars, doc is 100 chars so we get last 40
    result = extract_most_recent_messages(doc, 10)
    assert len(result) == 40
    assert result == "a" * 40


def test_extract_most_recent_messages_short_doc():
    result = extract_most_recent_messages("short", 100)
    assert result == "short"


def test_extract_front_matter_uses_front_matter():
    doc = "This is the full document text that should be ignored."
    front_matter = "Title\nTOC"
    result = extract_front_matter(doc, 100, front_matter=front_matter)
    assert result == "Title\nTOC"


def test_extract_front_matter_falls_back_to_first_n():
    doc = "Fallback document text"
    result = extract_front_matter(doc, 100)
    # No front_matter -> falls back to first_n
    assert result == "Fallback document text"


def test_extract_front_matter_truncates_long_front_matter():
    front_matter = "x" * 1000
    result = extract_front_matter("doc", 10, front_matter=front_matter)
    assert len(result) == 40  # 10 * 4


def test_strategies_dict_contains_all_keys():
    expected = {"first_n", "rolling_window", "most_recent", "front_matter"}
    assert set(STRATEGIES.keys()) == expected
