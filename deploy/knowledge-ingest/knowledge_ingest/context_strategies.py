"""
Context extraction strategies for enrichment.
Each strategy extracts the most relevant document context for a given content type.
"""
from __future__ import annotations


def extract_first_n_tokens(doc: str, n: int, **kwargs: object) -> str:
    """First N tokens of the document. Used for articles and unknown types."""
    max_chars = n * 4  # ~4 chars per token for Dutch/English
    return doc[:max_chars]


def extract_rolling_window(doc: str, n: int, *, chunk_index: int = 0, **kwargs: object) -> str:
    """Preceding text around the current chunk position.
    For transcripts where context is positional, not front-loaded."""
    max_chars = n * 4
    words = doc.split()
    total_words = len(words)
    if total_words == 0:
        return ""
    # Estimate word position from chunk_index (assume ~50 words per chunk)
    center = min(chunk_index * 50, total_words)
    window_words = n  # ~1 word per token approximation for window size
    start = max(0, center - window_words)
    end = min(total_words, center + window_words // 2)
    window_text = " ".join(words[start:end])
    return window_text[:max_chars]


def extract_most_recent_messages(doc: str, n: int, **kwargs: object) -> str:
    """Most recent N tokens from an email thread.
    Email threads have context at the end (most recent reply), not the beginning."""
    max_chars = n * 4
    return doc[-max_chars:] if len(doc) > max_chars else doc


def extract_front_matter(doc: str, n: int, *, front_matter: str | None = None, **kwargs: object) -> str:
    """Title + TOC extracted during PDF parsing.
    Falls back to first_n if no front_matter is provided."""
    if front_matter:
        max_chars = n * 4
        return front_matter[:max_chars]
    return extract_first_n_tokens(doc, n)


STRATEGIES: dict[str, callable] = {
    "first_n": extract_first_n_tokens,
    "rolling_window": extract_rolling_window,
    "most_recent": extract_most_recent_messages,
    "front_matter": extract_front_matter,
}
